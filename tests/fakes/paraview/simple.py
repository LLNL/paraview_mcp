"""Fake `paraview.simple`: just enough pipeline/view shape for the bridge's
exec engine and state summary (B-11..B-17) to run against without real
ParaView. Not general purpose -- only what the bridge actually calls.
"""

_registry = {}
_counter = {}
_active_source = None
_active_view = None
_views = []
_scene = None
_get_representation_calls = []


class FakeProxy:
    def __init__(self, xml_name, **props):
        self._xml_name = xml_name
        for k, v in props.items():
            setattr(self, k, v)

    def GetXMLName(self):
        return self._xml_name

    def GetRepresentation(self, *args, **kwargs):
        # Real paraview.simple creates a representation as a side effect
        # if one doesn't exist yet -- state summaries must never call
        # this (DESIGN.md 6.5). Tests assert this list stays empty.
        _get_representation_calls.append(self)
        return FakeRepresentation(self, _active_view)


class FakeUnserializableProxy(FakeProxy):
    """A proxy whose attributes make json.dumps() fail, to exercise the
    repr()-fallback path (B-12)."""

    def __init__(self, xml_name="Weird"):
        super().__init__(xml_name)

    def __repr__(self):
        return "<FakeUnserializableProxy %s>" % self._xml_name


class FakeRepresentation:
    def __init__(self, input_proxy, view):
        self.Input = input_proxy
        self.Visibility = 1


class FakeInteractor:
    def __init__(self):
        self._timers = {}
        self._next_timer_id = 1
        self._observers = {}

    def CreateRepeatingTimer(self, ms):
        tid = self._next_timer_id
        self._next_timer_id += 1
        self._timers[tid] = ms
        return tid

    def DestroyTimer(self, tid):
        self._timers.pop(tid, None)

    def AddObserver(self, event_name, callback):
        self._observers.setdefault(event_name, []).append(callback)

    def fire_timer_event(self):
        for cb in list(self._observers.get("TimerEvent", [])):
            cb(self, "TimerEvent")


class FakeView:
    def __init__(self, view_type="RenderView", with_interactor=True):
        self._xml_name = view_type
        self.ViewSize = [1084, 802]
        self.Representations = []
        self._interactor = FakeInteractor() if with_interactor else None

    def GetXMLName(self):
        return self._xml_name

    def GetInteractor(self):
        return self._interactor

    def GetRepresentation(self, *args, **kwargs):
        _get_representation_calls.append(self)
        return None


class FakeTimeKeeper:
    def __init__(self):
        self.Time = 0.0
        self.TimestepValues = []


class FakeAnimationScene:
    def __init__(self):
        self.TimeKeeper = FakeTimeKeeper()


def _reset(with_view=True):
    global _registry, _counter, _active_source, _active_view, _views, _scene
    _registry = {}
    _counter = {}
    _active_source = None
    _scene = FakeAnimationScene()
    _get_representation_calls.clear()
    if with_view:
        view = FakeView()
        _views = [view]
        _active_view = view
    else:
        _views = []
        _active_view = None


def _register(xml_name, proxy):
    global _active_source
    _counter[xml_name] = _counter.get(xml_name, 0) + 1
    name = "%s%d" % (xml_name, _counter[xml_name])
    _registry[(name, id(proxy))] = proxy
    _active_source = proxy
    return name


def Sphere(**kwargs):
    proxy = FakeProxy("Sphere", **kwargs)
    _register("Sphere", proxy)
    return proxy


def Cone(**kwargs):
    proxy = FakeProxy("Cone", **kwargs)
    _register("Cone", proxy)
    return proxy


def Show(proxy=None, view=None):
    if proxy is None:
        proxy = _active_source
    if view is None:
        view = _active_view
    rep = FakeRepresentation(proxy, view)
    view.Representations.append(rep)
    return rep


def Hide(proxy=None, view=None):
    if proxy is None:
        proxy = _active_source
    if view is None:
        view = _active_view
    for rep in view.Representations:
        if rep.Input is proxy:
            rep.Visibility = 0


def Delete(proxy):
    global _active_source
    for key in [k for k, v in _registry.items() if v is proxy]:
        del _registry[key]
    if _active_source is proxy:
        _active_source = None


def GetSources():
    return dict(_registry)


def GetActiveSource():
    return _active_source


def SetActiveSource(proxy):
    global _active_source
    _active_source = proxy


def GetActiveView():
    return _active_view


def GetRenderViews():
    return list(_views)


def GetAnimationScene():
    return _scene


def Render():
    pass


_reset()
