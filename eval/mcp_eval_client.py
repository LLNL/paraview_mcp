#!/usr/bin/env python3
"""
MCP Client with support for multiple LLM providers (Claude, OpenAI, etc.)
Now with image support for both providers
"""
import asyncio
import os
import json
import base64
from typing import Dict, Any, Optional, List, Union
from contextlib import AsyncExitStack
from abc import ABC, abstractmethod
import pdb
# Try to load .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, skip

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def call_api(prompt: str, options: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Promptfoo provider entry point
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        client = MCPClient()
        result = loop.run_until_complete(client.process_prompt(prompt, options))
        return result
    finally:
        loop.close()


class LLMProvider(ABC):
    """Abstract base class for LLM providers"""
    
    @abstractmethod
    async def create_completion(self, messages: List[Dict], tools: List[Dict], **kwargs) -> Dict:
        """Create a completion with tool support"""
        pass
    
    @abstractmethod
    def parse_tool_calls(self, response: Dict) -> List[Dict]:
        """Parse tool calls from response"""
        pass
    
    @abstractmethod
    def format_tool_result(self, tool_id: str, result: Any, images: Optional[List[Dict]] = None) -> Dict:
        """Format tool result for the provider"""
        pass


class ClaudeProvider(LLMProvider):
    """Claude/Anthropic provider"""
    
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        from anthropic import Anthropic
        self.client = Anthropic(api_key=api_key)
        self.model = model
    
    async def create_completion(self, messages: List[Dict], tools: List[Dict], **kwargs) -> Dict:
        """Create completion using Claude"""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=kwargs.get('max_tokens', 4096),
            messages=messages,
            tools=tools
        )
        return response
    
    def parse_tool_calls(self, response: Dict) -> List[Dict]:
        """Parse Claude's tool calls"""
        tool_calls = []
        for content in response.content:
            if content.type == 'tool_use':
                tool_calls.append({
                    'id': content.id,
                    'name': content.name,
                    'arguments': content.input,
                    'type': 'tool_use'
                })
        return tool_calls
    
    def format_tool_result(self, tool_id: str, result: Any, images: Optional[List[Dict]] = None) -> Dict:
        """Format tool result for Claude"""
        # If we have images, format as mixed content
        if images:
            tool_result_content = []
            
            # Add text content if present
            if result:
                tool_result_content.append({
                    "type": "text",
                    "text": str(result) if not isinstance(result, str) else result
                })
            
            # Add image content
            for img in images:
                tool_result_content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img['mime_type'],
                        "data": img['data']
                    }
                })
            
            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": tool_result_content
            }
        else:
            # Text-only result (backward compatibility)
            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": str(result) if not isinstance(result, str) else result
            }


class OpenAIProvider(LLMProvider):
    """OpenAI/OpenAI-compatible provider
    
    Note: OpenAI's support for images in tool results is not clearly documented.
    This implementation attempts to use content arrays with images when available,
    but may fall back to text-only format if the API doesn't support it.
    """
    
    def __init__(self, api_key: str, base_url: Optional[str] = None, model: str = "gpt-4", extra_headers: Optional[Dict] = None, verify_ssl: Union[bool, str] = True, use_proxy: bool = True):
        from openai import OpenAI
        import httpx
        import ssl
        
        # Clean up base_url - remove /chat/completions if present
        if base_url and base_url.endswith('/chat/completions'):
            base_url = base_url.replace('/chat/completions', '')
        
        # For LiteLLM proxy, we might need different headers
        default_headers = extra_headers or {}
        
        # Try different header formats for LiteLLM compatibility
        if base_url and ('litellm' in base_url.lower() or 'llnl.gov' in base_url):
            # LiteLLM often uses 'api-key' header instead of 'Authorization'
            default_headers['api-key'] = api_key
        
        # Configure httpx client
        client_kwargs = {
            "timeout": httpx.Timeout(30.0, connect=10.0),  # 30s total, 10s connect
        }
        
        # Handle proxy settings
        if not use_proxy:
            client_kwargs["proxy"] = None
        
        # Handle SSL verification
        if verify_ssl is False:
            client_kwargs["verify"] = False
        elif isinstance(verify_ssl, str) and os.path.exists(verify_ssl):
            # Use specific certificate file
            ssl_context = ssl.create_default_context(cafile=verify_ssl)
            client_kwargs["verify"] = ssl_context
        elif verify_ssl is True:
            # Try to use system certificates if available
            ssl_cert_file = os.environ.get('SSL_CERT_FILE') or os.environ.get('CURL_CA_BUNDLE')
            if ssl_cert_file and os.path.exists(ssl_cert_file):
                ssl_context = ssl.create_default_context(cafile=ssl_cert_file)
                client_kwargs["verify"] = ssl_context
        
        # Create httpx client with all settings
        http_client = httpx.Client(**client_kwargs)
        
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers if default_headers else None,
            http_client=http_client
        )
        self.model = model
    
    async def create_completion(self, messages: List[Dict], tools: List[Dict], **kwargs) -> Dict:
        """Create completion using OpenAI"""
        # Convert tools to OpenAI format
        openai_tools = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"]
                }
            })
        
        # Convert messages to OpenAI format
        openai_messages = self._convert_messages(messages)
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            tools=openai_tools if openai_tools else None,
            max_tokens=kwargs.get('max_tokens', 4096)
        )
        return response
    
    def _convert_messages(self, messages: List[Dict]) -> List[Dict]:
        """Convert messages to OpenAI format"""
        openai_messages = []
        for msg in messages:
            role = msg.get('role', '')
            
            if role == 'tool':
                # Handle tool messages
                tool_call_id = msg.get('tool_call_id', '')
                content = msg.get('content', '')
                has_images = False
                
                # Check if content has images
                if isinstance(content, list):
                    has_images = any(
                        item.get('type') == 'image_url' or 
                        (item.get('type') == 'image' and item.get('source', {}).get('type') == 'base64')
                        for item in content if isinstance(item, dict)
                    )
                
                # If tool message has images, we need to split it
                if has_images:
                    # First, add a placeholder tool message to satisfy OpenAI's requirement
                    text_content = []
                    image_content = []
                    
                    # Separate text and image content
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict):
                                if item.get('type') == 'text':
                                    text_content.append(item.get('text', ''))
                                elif item.get('type') in ['image_url', 'image']:
                                    image_content.append(item)
                    
                    # Add tool message with just text (placeholder if no text)
                    tool_text = ' '.join(text_content) if text_content else "Image result returned - see below"
                    openai_messages.append({
                        'role': 'tool',
                        'tool_call_id': tool_call_id,
                        'content': tool_text
                    })
                    
                    # Then add user message with the image
                    user_content = []
                    
                    # Add header to indicate this is a tool result image
                    user_content.append({
                        "type": "text",
                        "text": f"[Tool Result Image for call {tool_call_id}]:"
                    })
                    
                    # Add any text content
                    if text_content:
                        user_content.append({
                            "type": "text",
                            "text": ' '.join(text_content)
                        })
                    
                    # Process image content
                    for item in image_content:
                        if item.get('type') == 'image_url':
                            # Already in correct format
                            user_content.append(item)
                        elif item.get('type') == 'image':
                            # Convert from Claude format
                            source = item.get('source', {})
                            if source.get('type') == 'base64':
                                user_content.append({
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{source.get('media_type', 'image/png')};base64,{source.get('data', '')}"
                                    }
                                })
                    
                    openai_messages.append({
                        'role': 'user',
                        'content': user_content
                    })
                else:
                    # No images, use standard tool message format
                    tool_msg = {
                        'role': 'tool',
                        'tool_call_id': tool_call_id
                    }
                    
                    if isinstance(content, list):
                        # Extract text content from list
                        text_parts = []
                        for item in content:
                            if isinstance(item, dict) and item.get('type') == 'text':
                                text_parts.append(item.get('text', ''))
                        tool_msg['content'] = ' '.join(text_parts) if text_parts else str(content)
                    else:
                        # Text-only content
                        tool_msg['content'] = str(content)
                    
                    openai_messages.append(tool_msg)
                
            elif role in ['user', 'assistant', 'system']:
                # Handle different content types
                content = msg.get('content', '')
                
                if isinstance(content, str):
                    openai_messages.append({
                        'role': role,
                        'content': content
                    })
                elif isinstance(content, list):
                    # Handle mixed content (text, tool results, etc.)
                    content_parts = []
                    tool_calls = []
                    
                    for item in content:
                        if isinstance(item, dict):
                            if item.get('type') == 'text':
                                content_parts.append(item.get('text', ''))
                            elif item.get('type') == 'tool_use':
                                tool_calls.append({
                                    'id': item.get('id', ''),
                                    'type': 'function',
                                    'function': {
                                        'name': item.get('name', ''),
                                        'arguments': json.dumps(item.get('input', {}))
                                    }
                                })
                            elif item.get('type') == 'tool_result':
                                # Convert tool results to function messages
                                # Handle both text-only and mixed content results
                                tool_content = item.get('content', '')
                                if isinstance(tool_content, list):
                                    # Mixed content with possible images
                                    # Try to preserve content array format if possible
                                    content_array = []
                                    text_parts = []  # Fallback for text-only
                                    
                                    for content_item in tool_content:
                                        if content_item.get('type') == 'text':
                                            content_array.append({
                                                "type": "text",
                                                "text": content_item.get('text', '')
                                            })
                                            text_parts.append(content_item.get('text', ''))
                                        elif content_item.get('type') == 'image':
                                            source = content_item.get('source', {})
                                            if source.get('type') == 'base64':
                                                # Try to use image_url format
                                                content_array.append({
                                                    "type": "image_url",
                                                    "image_url": {
                                                        "url": f"data:{source.get('media_type', 'image/png')};base64,{source.get('data', '')}"
                                                    }
                                                })
                                                text_parts.append(f"[IMAGE: {source.get('media_type', 'unknown')}]")
                                    
                                    # Try content array format first (may work with newer API versions)
                                    # If it fails, the API will return an error and you can fall back to text-only
                                    openai_messages.append({
                                        'role': 'tool',
                                        'content': content_array if content_array else '\n'.join(text_parts),
                                        'tool_call_id': item.get('tool_use_id', '')
                                    })
                                else:
                                    # Text-only content
                                    openai_messages.append({
                                        'role': 'tool',
                                        'content': str(tool_content),
                                        'tool_call_id': item.get('tool_use_id', '')
                                    })
                        else:
                            # Handle other content types
                            content_parts.append(str(item))
                    
                    if content_parts or tool_calls:
                        msg_dict = {'role': role}
                        if content_parts:
                            msg_dict['content'] = '\n'.join(content_parts)
                        if tool_calls:
                            msg_dict['tool_calls'] = tool_calls
                        if msg_dict.get('content') or msg_dict.get('tool_calls'):
                            openai_messages.append(msg_dict)
                elif content is None:
                    # Handle messages with no content (e.g., tool-only messages)
                    openai_messages.append({
                        'role': role,
                        'content': ''
                    })
                    
                # Handle tool_calls if present
                if 'tool_calls' in msg and msg['tool_calls']:
                    # Ensure we have a message dict
                    if not openai_messages or openai_messages[-1]['role'] != role:
                        openai_messages.append({'role': role, 'content': ''})
                    
                    # Add tool_calls to the last message
                    openai_messages[-1]['tool_calls'] = msg['tool_calls']
        
        # Debug print
        # print("openAI message", openai_messages)
        return openai_messages
    
    def parse_tool_calls(self, response) -> List[Dict]:
        """Parse OpenAI's tool calls"""
        tool_calls = []
        message = response.choices[0].message
        
        if hasattr(message, 'tool_calls') and message.tool_calls:
            for tool_call in message.tool_calls:
                tool_calls.append({
                    'id': tool_call.id,
                    'name': tool_call.function.name,
                    'arguments': json.loads(tool_call.function.arguments),
                    'type': 'function'
                })
        return tool_calls
    
    def format_tool_result(self, tool_id: str, result: Any, images: Optional[List[Dict]] = None) -> Dict:
        """Format tool result for OpenAI
        
        Note: If the API rejects content arrays in tool messages, you may need to:
        1. Fall back to text-only format
        2. Upload images to a URL service and reference them
        3. Include images in a subsequent user message instead
        """
        # Try to use content array format similar to Claude if images are present
        if images:
            # Attempt to use content array format (may or may not be supported)
            content_array = []
            
            # Add text content if present
            if result:
                content_array.append({
                    "type": "text",
                    "text": str(result) if not isinstance(result, str) else result
                })
            
            # Add image content
            for img in images:
                content_array.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{img['mime_type']};base64,{img['data']}"
                    }
                })
            
            # Try content array format first
            return {
                "role": "tool",
                "content": content_array,
                "tool_call_id": tool_id
            }
        else:
            # Text-only result (standard format)
            return {
                "role": "tool",
                "content": str(result) if not isinstance(result, str) else result,
                "tool_call_id": tool_id
            }


class MCPClient:
    def __init__(self):
        """Initialize MCP client"""
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.llm_provider: Optional[LLMProvider] = None
        
    def _initialize_llm_provider(self, config: Dict[str, Any]):
        """Initialize the appropriate LLM provider based on config"""
        provider = config.get('provider', 'claude').lower()
        
        if provider == 'claude':
            api_key = config.get('apiKey') or os.environ.get('ANTHROPIC_API_KEY')
            if api_key:
                model = config.get('model', 'claude-sonnet-4-20250514')
                self.llm_provider = ClaudeProvider(api_key, model)
        elif provider in ['openai', 'local', 'litellm']:
            api_key = config.get('apiKey') or os.environ.get('OPENAI_API_KEY', 'dummy-key')
            base_url = config.get('baseUrl') or os.environ.get('OPENAI_BASE_URL')
            model = config.get('model', 'gpt-4o')
            
            # SSL verification setting (default True, can be disabled for internal/self-signed certs)
            verify_ssl = config.get('verifySSL', True)
            
            # Proxy setting (default True, can be disabled)
            use_proxy = config.get('useProxy', True)
            
            self.llm_provider = OpenAIProvider(api_key, base_url, model, None, verify_ssl, use_proxy)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
        
    async def connect_to_server(self, server_config: Dict[str, Any]):
        """Connect to an MCP server"""
        command = server_config.get('command', 'python')
        args = server_config.get('args', [])
        cwd = server_config.get('cwd')
        env = server_config.get('env')
        
        server_params = StdioServerParameters(
            command=command,
            args=args,
            cwd=cwd,
            env=env
        )
        
        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self.stdio, self.write = stdio_transport
        
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(self.stdio, self.write)
        )
        
        await self.session.initialize()
        
    async def process_with_llm(self, prompt: str) -> str:
        """Process prompt using the configured LLM provider"""
        if not self.llm_provider:
            return "Error: No LLM provider configured"
            
        if not self.session:
            return "Error: No MCP session available"
        
        # Get available tools from MCP server
        tools_response = await self.session.list_tools()
        available_tools = [{
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema
        } for tool in tools_response.tools]
        
        # Initialize messages
        messages = [{"role": "user", "content": prompt}]
        
        # Process output
        final_text: List[str] = []                       # UPDATED
        collected_images: List[Dict[str, str]] = []      # NEW
        
        # Add logging
        final_text.append(f"[MCP Server Connected - {len(available_tools)} tools available]")
        tool_names = [tool['name'] for tool in available_tools]
        final_text.append(f"[Available tools: {', '.join(tool_names)}]")
        final_text.append("")
        
        # Log provider type
        provider_type = type(self.llm_provider).__name__
        final_text.append(f"[Using LLM Provider: {provider_type}]")
        final_text.append("")
        
        # Continue conversation until LLM stops using tools
        max_iterations = 15  # Reduced from 20 to prevent loops
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            
            try:
                # Get LLM's response
                response = await self.llm_provider.create_completion(messages, available_tools)
                
                # Extract text content based on provider
                if isinstance(self.llm_provider, ClaudeProvider):
                    # Claude response handling
                    assistant_content = []
                    has_tool_use = False
                    
                    for content in response.content:
                        if content.type == 'text':
                            final_text.append(content.text)
                            assistant_content.append(content)
                        elif content.type == 'tool_use':
                            has_tool_use = True
                            assistant_content.append(content)
                    
                    messages.append({
                        "role": "assistant",
                        "content": assistant_content
                    })
                    
                elif isinstance(self.llm_provider, OpenAIProvider):
                    # OpenAI response handling
                    message = response.choices[0].message
                    has_tool_use = bool(getattr(message, 'tool_calls', None))
                    
                    # Check if there's any actual content to add
                    if message.content:
                        final_text.append(message.content)
                    
                    # Build assistant message
                    assistant_msg = {"role": "assistant"}
                    if message.content:
                        assistant_msg["content"] = message.content
                    else:
                        # OpenAI sometimes returns None content with tool calls
                        assistant_msg["content"] = ""
                        
                    if has_tool_use:
                        assistant_msg["tool_calls"] = [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            } for tc in message.tool_calls
                        ]
                    messages.append(assistant_msg)
                
                # Process tool calls if any
                if has_tool_use:
                    tool_calls = self.llm_provider.parse_tool_calls(response)
                    tool_results = []
                    
                    for tool_call in tool_calls:
                        tool_name = tool_call['name']
                        tool_args = tool_call['arguments']
                        tool_id = tool_call['id']
                        
                        final_text.append(f"\n[MCP Tool Call: {tool_name}]")
                        final_text.append(f"[Arguments: {tool_args}]")
                        
                        try:
                            result = await self.session.call_tool(tool_name, tool_args)
                            final_text.append(f"[Tool call successful]")
                            
                            # Extract result content (including images)
                            result_text = ""
                            result_images = []
                            
                            if result and result.content:
                                if isinstance(result.content, list):
                                    for item in result.content:
                                        if hasattr(item, 'text'):
                                            result_text += item.text + "\n"
                                        elif hasattr(item, 'type') and item.type == 'image':
                                            # Handle image content
                                            result_images.append({
                                                'data': item.data,
                                                'mime_type': item.mimeType
                                            })
                                            result_text += f"[IMAGE: {item.mimeType}]\n"
                                        else:
                                            result_text += str(item) + "\n"
                                elif hasattr(result.content, 'text'):
                                    result_text = result.content.text
                                elif hasattr(result.content, 'type') and result.content.type == 'image':
                                    result_images.append({
                                        'data': result.content.data,
                                        'mime_type': result.content.mimeType
                                    })
                                    result_text = f"[IMAGE: {result.content.mimeType}]"
                                else:
                                    result_text = str(result.content)
                            
                            final_text.append("")
                            
                            # ----  NEW: keep images so we can embed later  ----
                            if result_images:
                                collected_images.extend(result_images)
                            # ---------------------------------------------------
                            
                            # Format result for provider
                            tool_results.append(
                                self.llm_provider.format_tool_result(
                                    tool_id, 
                                    result_text.strip() if result_text else "",
                                    result_images if result_images else None
                                )
                            )
                            
                        except Exception as e:
                            final_text.append(f"[Error: {str(e)}]")
                            tool_results.append(
                                self.llm_provider.format_tool_result(
                                    tool_id, 
                                    f"Error: {str(e)}"
                                )
                            )
                    
                    # If there were tool uses, add the results and continue
                    if isinstance(self.llm_provider, ClaudeProvider):
                        messages.append({
                            "role": "user",
                            "content": tool_results
                        })
                    elif isinstance(self.llm_provider, OpenAIProvider):
                        # OpenAI adds tool results as separate messages
                        for result in tool_results:
                            messages.append(result)
                else:
                    # No more tool uses, we're done
                    break
                    
            except Exception as e:
                final_text.append(f"\n[Error during LLM call: {type(e).__name__}: {str(e)}]")
                break
        
        if iteration >= max_iterations:
            final_text.append("\n[Warning: Maximum iteration limit reached]")
        
        # ----  NEW: embed collected images in Markdown  ----
        if collected_images:
            final_text.append("\n### Images returned by tools\n")
            for idx, img in enumerate(collected_images, 1):
                data = img.get("data")
                if isinstance(data, str):
                    b64 = data
                else:
                    b64 = base64.b64encode(data).decode()
                mime = img.get("mime_type", "image/png")
                final_text.append(f"![tool-image-{idx}](data:{mime};base64,{b64})")
        # ---------------------------------------------------
        
        return "\n".join(final_text)
        
    async def process_prompt(self, prompt: str, options: Dict[str, Any]) -> Dict[str, Any]:
        """Process a prompt using the MCP server and LLM"""
        try:
            config = options.get('config', {})
            
            # Initialize LLM provider
            self._initialize_llm_provider(config)
            
            # Get MCP server configuration
            mcp_config = config.get('mcp', {})
            server_config = mcp_config.get('server', {})
            
            if isinstance(server_config, list):
                server_config = server_config[0]
            
            # Connect to server
            await self.connect_to_server(server_config)
            
            # Process with LLM
            if self.llm_provider:
                output = await self.process_with_llm(prompt)
            else:
                output = "Error: No LLM provider available"
            
            return {"output": output}
            
        except Exception as e:
            import traceback
            return {
                "output": f"Error: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
            }
        finally:
            await self.cleanup()
            
    async def cleanup(self):
        """Clean up resources"""
        await self.exit_stack.aclose()

'''

promptfoo eval -c eval/test_general.yaml --no-cache

'''
