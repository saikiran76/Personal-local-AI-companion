import asyncio
import logging
from typing import List, Dict, Any
from pydantic import BaseModel

# In 2026, the standard adapter libraries handle the heavy lifting for stdio connections
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

class MCPServerConfig(BaseModel):
    command: str
    args: List[str]
    enabled: bool

class MCPManager:
    """
    Manages the lifecycle of out-of-process MCP Servers (Filesystem, Puppeteer, Git, etc.)
    and translates them into LangChain-compatible tools.
    """
    def __init__(self):
        self.client = None
        self._tools: List[BaseTool] = []
        
        # Central registry of available integrations. 
        # In a real app, this is loaded from the user's Settings/Integrations UI preferences.
        self.server_registry: Dict[str, MCPServerConfig] = {
            "filesystem": MCPServerConfig(
                command="npx", 
                args=["-y", "@modelcontextprotocol/server-filesystem", "~/.desktop-companion/workspace"], 
                enabled=True
            ),
            "fetch": MCPServerConfig(
                command="npx", 
                args=["-y", "@modelcontextprotocol/server-fetch"], 
                enabled=True
            ),
            "sqlite": MCPServerConfig(
                command="npx", 
                args=["-y", "@modelcontextprotocol/server-sqlite", "~/.desktop-companion/database.sqlite"], 
                enabled=False # Disabled by default until user opts-in via Integrations Screen
            )
        }

    async def start(self):
        """Starts the enabled MCP servers and connects the adapters."""
        logger.info("Initializing MCP Subsystems...")
        
        # Filter only servers the user has enabled
        active_servers = {
            name: {"command": config.command, "args": config.args}
            for name, config in self.server_registry.items() if config.enabled
        }
        
        if not active_servers:
            logger.info("No MCP servers enabled.")
            return

        try:
            # MultiServerMCPClient handles spinning up the stdio processes and catching their crashes
            self.client = MultiServerMCPClient(active_servers)
            await self.client.start()
            
            # Dynamically fetch the schemas from the connected servers
            self._tools = await self.client.get_tools()
            logger.info(f"Successfully loaded {len(self._tools)} MCP tools: {[t.name for t in self._tools]}")
            
        except Exception as e:
            logger.error(f"Failed to initialize MCP cluster: {e}")
            # Fails gracefully. The LLM will just chat normally without tools.
            self._tools = []

    async def stop(self):
        """Gracefully shuts down all child processes to prevent zombie Node/Python processes."""
        if self.client:
            logger.info("Shutting down MCP cluster...")
            await self.client.stop()

    def get_agent_tools(self) -> List[BaseTool]:
        """Returns the list of robust LangChain tools for the Agent loop."""
        return self._tools

# Singleton instance to be used across the FastAPI app
mcp_manager = MCPManager()