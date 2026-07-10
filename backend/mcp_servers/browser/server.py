"""
MCP Browser Server — browser automation and web content fetching via MCP protocol.

Communicates via stdio using the MCP protocol (JSON-RPC).
Reads stdin synchronously to avoid Windows ProactorEventLoop bugs.
"""

import json
import logging
import sys
import webbrowser

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("browser")


def handle_request(request: dict) -> dict:
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "browser", "version": "0.1.0"},
            },
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "open_browser",
                        "description": "Open a URL in the default browser",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"url": {"type": "string"}},
                            "required": ["url"],
                        },
                    },
                    {
                        "name": "search_web",
                        "description": "DO NOT USE for getting information. This ONLY opens a browser tab. Use search_and_fetch instead to get readable content.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                    {
                        "name": "fetch_page",
                        "description": "Fetch a web page and extract its main text content. Returns cleaned article text. Does not follow links or render JavaScript.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "description": "The URL to fetch"},
                            },
                            "required": ["url"],
                        },
                    },
                    {
                        "name": "search_and_fetch",
                        "description": "USE THIS to search the web and get readable content. Returns actual article text you can read and summarize. Always prefer this over search_web.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "Search query (e.g. 'python async best practices')"},
                            },
                            "required": ["query"],
                        },
                    },
                ],
            },
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "open_browser":
            url = arguments.get("url", "")
            webbrowser.open(url)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Opened: {url}"}],
                },
            }

        elif tool_name == "search_web":
            query = arguments.get("query", "")
            url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            webbrowser.open(url)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Searched for: {query}"}],
                },
            }

        elif tool_name == "fetch_page":
            url = arguments.get("url", "")
            if not url:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -1, "message": "URL is required"},
                }

            # Ensure URL has a scheme
            if not url.startswith(("http://", "https://")):
                url = "https://" + url

            try:
                import httpx
            except ImportError:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -1, "message": "httpx not installed"},
                }

            try:
                response = httpx.get(
                    url,
                    timeout=15,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; Luna/1.0)"},
                )
                response.raise_for_status()
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -1, "message": f"Failed to fetch: {e}"},
                }

            # Parse HTML and extract main content
            try:
                from bs4 import BeautifulSoup
            except ImportError:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -1, "message": "beautifulsoup4 not installed"},
                }

            soup = BeautifulSoup(response.text, "html.parser")

            # Remove non-content elements
            for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"]):
                tag.decompose()

            # Try to find main content area
            main_content = (
                soup.find("article")
                or soup.find("main")
                or soup.find(attrs={"role": "main"})
                or soup.find("body")
            )

            if main_content is None:
                text = soup.get_text(separator="\n", strip=True)
            else:
                text = main_content.get_text(separator="\n", strip=True)

            # Clean up whitespace
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            text = "\n".join(lines)

            # Cap at 8000 characters
            if len(text) > 8000:
                text = text[:8000] + "..."

            if not text:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": "Page fetched but no readable text content found."}],
                    },
                }

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                },
            }

        elif tool_name == "search_and_fetch":
            query = arguments.get("query", "")
            if not query:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -1, "message": "Query is required"},
                }

            try:
                import httpx
                from bs4 import BeautifulSoup
            except ImportError:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -1, "message": "httpx and beautifulsoup4 required"},
                }

            # Step 1: Search DuckDuckGo HTML-only endpoint
            search_url = "https://html.duckduckgo.com/html/"
            try:
                search_resp = httpx.post(
                    search_url,
                    data={"q": query},
                    timeout=15,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; Luna/1.0)"},
                )
                search_resp.raise_for_status()
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -1, "message": f"Search failed: {e}"},
                }

            # Step 2: Extract result links, skipping aggregator/news-index sites
            search_soup = BeautifulSoup(search_resp.text, "html.parser")
            result_links = search_soup.select(".result__a")

            # Aggregator sites that return headlines, not articles — skip them
            _SKIP_DOMAINS = {
                "news.google.com", "bing.com/news", "news.yahoo.com",
                "feedly.com", "flipboard.com", "newslookup.com",
            }

            result_url = None
            result_title = None
            for link in result_links:
                href = link.get("href", "")
                # Check if the URL is from an aggregator
                skip = False
                for domain in _SKIP_DOMAINS:
                    if domain in href:
                        skip = True
                        break
                if not skip and href:
                    result_url = href
                    result_title = link.get_text(strip=True)
                    break

            if not result_url:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"No results found for: {query}"}],
                    },
                }

            # Step 3: Fetch the result page content (reuse fetch_page extraction logic)
            try:
                page_resp = httpx.get(
                    result_url,
                    timeout=15,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; Luna/1.0)"},
                )
                page_resp.raise_for_status()
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Found result: {result_title}\n{result_url}\n\nFailed to fetch content: {e}"}],
                    },
                }

            # Step 4: Extract text content
            page_soup = BeautifulSoup(page_resp.text, "html.parser")
            for tag in page_soup.find_all(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"]):
                tag.decompose()

            main_content = (
                page_soup.find("article")
                or page_soup.find("main")
                or page_soup.find(attrs={"role": "main"})
                or page_soup.find("body")
            )

            if main_content is None:
                text = page_soup.get_text(separator="\n", strip=True)
            else:
                text = main_content.get_text(separator="\n", strip=True)

            lines = [line.strip() for line in text.splitlines() if line.strip()]
            text = "\n".join(lines)

            if len(text) > 8000:
                text = text[:8000] + "..."

            header = f"Search result: {result_title}\nSource: {result_url}\n\n"
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": header + (text or "No readable text content found.")}],
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main():
    """Run the MCP server over stdio — synchronous reads."""
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            request = json.loads(line)
            response = handle_request(request)
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError:
            continue
        except BrokenPipeError:
            break
        except Exception:
            break


if __name__ == "__main__":
    main()
