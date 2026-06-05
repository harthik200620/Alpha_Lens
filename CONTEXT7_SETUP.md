# Context7 MCP Setup Guide

Context7 is now installed in the Alpha_Lens Claude Code environment. Follow these steps to enable it fully.

## Quick Setup (5 minutes)

### Step 1: Get API Key
1. Visit [context7.com/dashboard](https://context7.com/dashboard)
2. Sign up (free tier available)
3. Generate an API key
4. Copy the key

### Step 2: Set Environment Variable (Choose One)

#### Option A: For Local Development (`.env` file)
Add to `C:\Project rohan\Alpha_Lens\.env`:
```
CONTEXT7_API_KEY=your_api_key_here
```

#### Option B: For Claude Code Environment
Set via environment (OS level or Claude settings):
```bash
# Windows
set CONTEXT7_API_KEY=your_api_key_here

# Or in PowerShell
$env:CONTEXT7_API_KEY='your_api_key_here'
```

#### Option C: For Render Deployment (Production) ✅ DONE
The `CONTEXT7_API_KEY` has been added to `render.yaml`:
```yaml
- key: CONTEXT7_API_KEY
  sync: false
```
You've already set this value in Render's environment dashboard. The deployment will automatically use it.

### Step 3: Put the key inline in `.mcp.json`

> ⚠️ **Important:** Render's env / the project `.env` only power the Flask app —
> they do **not** feed Claude Code's MCP connection. Also, `${CONTEXT7_API_KEY}`
> expansion from settings `env` proved unreliable, so the key goes **inline** in the
> gitignored `.mcp.json`.

`.mcp.json` (gitignored — safe for secrets):
```json
{
  "mcpServers": {
    "context7": {
      "type": "http",
      "url": "https://mcp.context7.com/mcp",
      "headers": { "CONTEXT7_API_KEY": "ctx7sk-your-real-key-here" }
    }
  }
}
```
Keep `enabledMcpjsonServers: ["context7"]` in `.claude/settings.local.json` so the
server is trusted automatically.

### Step 4: Verify Installation

**Fully quit and reopen** Claude Code (MCP servers load at startup), then run `/mcp`.
You should see **context7 — connected**, exposing:
- ✅ `resolve-library-id`
- ✅ `query-docs`

## What You Get

### Instant Access to Documentation
- **Flask** — Request handling, routing, blueprints, configuration
- **Google Gemini API** — Model selection, prompt engineering, function calling
- **yfinance** — Historical data, real-time tickers, NSE/BSE symbols
- **SendGrid** — Email sending, OTP templates, personalization
- **BeautifulSoup4** — HTML parsing, CSS selectors, tag navigation
- **feedparser** — RSS parsing, feed structure, item extraction
- **And 20+ more libraries** — pandas, numpy, requests, etc.

### How It Works
1. Claude asks Context7: "What's the Flask API for handling POST requests?"
2. Context7 resolves "Flask" → official library ID
3. Context7 returns **real, version-specific documentation** (no hallucinations)
4. Claude provides accurate, actionable code examples

## Example Usage

### Before (Without Context7)
```
You: "How do I send an OTP email with SendGrid in Flask?"
Claude: [May hallucinate API or suggest deprecated methods]
```

### After (With Context7)
```
You: "How do I send an OTP email with SendGrid in Flask?"
Claude: [Fetches real SendGrid docs via Context7]
Claude: "Here's the exact API for SendGrid Mail..."
[Provides accurate, version-specific code]
```

## Supported Libraries in Alpha_Lens

| Category | Libraries |
|----------|-----------|
| **Web Framework** | Flask, Flask-Compress, Werkzeug, Gunicorn |
| **AI/ML APIs** | google-genai (Gemini), OpenAI |
| **Market Data** | yfinance, pandas, numpy |
| **Email/Communication** | SendGrid, pytz |
| **Web Scraping** | feedparser, requests, BeautifulSoup4 |
| **Authentication** | google-auth, python-dateutil |
| **Database** | SQLite3 (built-in), psycopg2 |
| **Utilities** | python-dotenv, logzero |

## Verification Steps

### ✅ Check Local Setup
1. Verify the MCP server is registered:
   ```bash
   grep -A6 context7 .mcp.json
   ```
   Should show the `https://mcp.context7.com/mcp` HTTP server.

2. Verify your key is set locally (NOT the placeholder):
   ```bash
   grep CONTEXT7_API_KEY .claude/settings.local.json
   ```
   Should show your real key, not `PASTE_YOUR_CONTEXT7_API_KEY_HERE`.

3. Run `/mcp` in Claude Code — `context7` should be **connected**.

### ✅ Test Context7 in Claude Code
Ask Claude Code:
```
"Look up Flask documentation using Context7"
```
Or:
```
"Show me the Gemini API documentation via Context7"
```

If Context7 is working, Claude should fetch real, version-specific documentation.

## Troubleshooting

### API Key Not Working
- Verify the key is copied correctly from [context7.com/dashboard](https://context7.com/dashboard)
- Confirm it's in `.claude/settings.local.json` → `env.CONTEXT7_API_KEY` (the project `.env`/Render do NOT feed the MCP)
- Restart Claude Code after changing the key so the MCP reconnects

### Context7 Tool Not Found
- Verify `.claude/settings.local.json` contains:
  ```json
  "mcp__context7__resolve-library-id",
  "mcp__context7__query-docs"
  ```
- Check for syntax errors in the JSON file
- Restart Claude Code

### Documentation Not Detailed Enough
- Provide more specific queries: "How do I use SendGrid to send templated emails?" vs "SendGrid docs"
- Include library name: "Gemini API" vs just "API"
- Mention version/context when relevant

## Advanced Usage

### Directly Query by Library ID
If you know the Context7 library ID (e.g., `/sendgrid/sendgrid`), you can query directly:
- "Show me docs for `/sendgrid/sendgrid` on Mail Send"
- "Query `/google/gemini-api` for streaming responses"

### Version-Specific Docs
Context7 automatically detects versions from `requirements.txt`:
- Flask 3.1.3 → Returns docs for Flask 3.1.x
- google-genai 2.3.0 → Returns docs for Gemini API v2.x
- yfinance 1.3.0 → Returns yfinance 1.3.x API

## Resources

- **Context7 Official**: [context7.com](https://context7.com)
- **GitHub**: [upstash/context7](https://github.com/upstash/context7)
- **MCP Registry**: [claudemcp.com/servers/context7](https://www.claudemcp.com/servers/context7)
- **Dashboard**: [context7.com/dashboard](https://context7.com/dashboard)

---

**Status**: ✅ Context7 MCP installed and ready to use in Claude Code for Alpha_Lens project
