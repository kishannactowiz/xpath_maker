"""
XPath Maker UI
--------------
Run locally:  python app.py          ->  http://127.0.0.1:8000
Hosted:       the platform sets PORT  ->  listens on 0.0.0.0:$PORT

Requirements:
    pip install lxml
    xpath_maker.py in the same folder
"""

import json
import os
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lxml import html as lhtml
from xpath_maker import xpath_maker


# On a host (Hugging Face Spaces, Render ...) the platform provides PORT.
HOSTED = "PORT" in os.environ
HOST = os.environ.get("HOST", "0.0.0.0" if HOSTED else "127.0.0.1")
PORT = int(os.environ.get("PORT", 8000))
MAX_BODY = 5 * 1024 * 1024          # reject pasted HTML bigger than 5 MB


# --------------------------------------------------
# Generate XPath
# --------------------------------------------------
def generate(html_text, items, opts):
    if not html_text.strip():
        raise ValueError("Please paste HTML source code first.")

    texts = {
        str(i): item["text"]
        for i, item in enumerate(items)
        if item.get("text", "").strip()
    }

    if not texts:
        raise ValueError("Please select and add some text first.")

    limit = opts.get("limit", 2)
    limit = None if limit in (None, 0, "all") else int(limit)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".html",
        delete=False,
        encoding="utf-8"
    ) as file:
        file.write(html_text)
        path = file.name

    try:
        found = xpath_maker(
            path,
            texts,
            exact=bool(opts.get("exact", False)),
            with_text=bool(opts.get("with_text", True)),
            anchor=bool(opts.get("anchor", True)),
            use_label=bool(opts.get("use_label", True)),
            index=bool(opts.get("index", False)),
            limit=limit,
        )

        root = lhtml.parse(
            path,
            lhtml.HTMLParser(encoding="utf-8")
        ).getroot()

    finally:
        if os.path.exists(path):
            os.remove(path)

    output = {}

    for key, xpaths in found.items():
        block = []

        for xp in xpaths:
            try:
                nodes = root.xpath(xp)
                values = []

                for node in nodes:
                    if isinstance(node, str):
                        value = node
                    else:
                        value = node.text_content()

                    value = " ".join(str(value).split())

                    if value:
                        values.append(value)

                block.append({
                    "xpath": xp,
                    "count": len(nodes),
                    "values": values[:6]
                })

            except Exception as error:
                block.append({
                    "xpath": xp,
                    "count": 0,
                    "values": [],
                    "error": str(error)
                })

        output[key] = block

    return output


# --------------------------------------------------
# Frontend
# --------------------------------------------------
PAGE = r"""<!DOCTYPE html>
<html lang="en">

<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>XPath Maker</title>

<style>
:root {
    --bg: #f4f5f8;
    --card: #ffffff;
    --text: #202534;
    --muted: #687083;
    --line: #dce1ea;
    --accent: #2f5bea;
    --code: #eef1f7;
    --success: #12805c;
    --error: #b3261e;
}

body.dark-mode {
    --bg: #151821;
    --card: #202431;
    --text: #e8eaf0;
    --muted: #a5adbd;
    --line: #3b4354;
    --accent: #7895ff;
    --code: #292f3e;
    --success: #54d6a0;
    --error: #ff8b83;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font: 15px/1.5 Arial, sans-serif;
    color: var(--text);
    background: var(--bg);
    transition: background 0.2s, color 0.2s;
}

.bar {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    padding: 12px 18px;
    background: var(--card);
    border-bottom: 1px solid var(--line);
}

h1 {
    margin: 0;
    font-size: 19px;
}

h2 {
    margin: 0 0 10px;
    font-size: 16px;
}

.grow {
    flex: 1;
}

.muted {
    color: var(--muted);
    font-size: 13px;
}

button {
    padding: 8px 13px;
    border: 1px solid var(--line);
    border-radius: 7px;
    background: var(--card);
    color: var(--text);
    font: inherit;
    cursor: pointer;
}

button:hover:not(:disabled) {
    border-color: var(--accent);
}

button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

button.primary {
    background: var(--accent);
    border-color: var(--accent);
    color: white;
    font-weight: 600;
}

body.dark-mode button.primary {
    color: #111827;
}

#editor-wrap {
    padding: 18px;
}

#html {
    display: block;
    width: 100%;
    height: 55vh;
    min-height: 280px;
    resize: vertical;
    padding: 14px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--card);
    color: var(--text);
    font: 13px/1.55 Consolas, monospace;
    tab-size: 4;
}

#html:focus {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
}

#selectionBar {
    display: none;
    margin-top: 12px;
    padding: 12px;
    border: 1px solid var(--line);
    border-radius: 9px;
    background: var(--card);
}

#selectedText {
    display: block;
    margin: 8px 0 12px;
    padding: 9px;
    border-radius: 6px;
    background: var(--code);
    font-family: Consolas, monospace;
    overflow-wrap: anywhere;
    white-space: pre-wrap;
}

#options {
    padding: 12px 18px;
    border-bottom: 1px solid var(--line);
    background: var(--card);
}

#optionPanel {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    align-items: center;
}

#optionPanel label {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 13px;
}

select {
    padding: 5px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--card);
    color: var(--text);
}

#tray {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    min-height: 48px;
    padding: 12px 18px;
    background: var(--card);
    border-bottom: 1px solid var(--line);
}

.chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    max-width: 100%;
    padding: 5px 8px 5px 11px;
    border-radius: 7px;
    background: var(--code);
    font-size: 13px;
}

.chip span {
    overflow-wrap: anywhere;
}

.chip button {
    padding: 0 4px;
    border: 0;
    background: transparent;
    color: var(--muted);
}

#results {
    width: 100%;
    max-width: 1100px;
    margin: 0 auto;
    padding: 18px;
}

.result {
    margin-bottom: 14px;
    padding: 15px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--card);
}

.result h3 {
    margin: 0 0 5px;
    font-size: 15px;
    overflow-wrap: anywhere;
}

.xpath-box {
    margin-top: 10px;
    padding: 11px;
    border-radius: 7px;
    background: var(--code);
}

.xpath-code {
    display: block;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font: 13px/1.5 Consolas, monospace;
}

.meta {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 9px;
    font-size: 12px;
}

.meta button {
    padding: 4px 9px;
    font-size: 12px;
}

.ok {
    color: var(--success);
}

.bad {
    color: var(--error);
}

.values {
    margin-top: 7px;
    color: var(--muted);
    font-size: 12px;
    overflow-wrap: anywhere;
}

@media (max-width: 600px) {
    #html {
        height: 48vh;
    }

    .bar,
    #editor-wrap,
    #results {
        padding: 10px;
    }
}
</style>
</head>

<body>

<!-- Header -->
<div class="bar">
    <h1>XPath Maker</h1>

    <span class="grow"></span>

    <button id="themeButton" type="button">
        Dark Mode
    </button>

    <button id="clearButton" type="button">
        Clear All
    </button>
</div>

<!-- HTML source editor -->
<div id="editor-wrap">

    <textarea
        id="html"
        placeholder="Paste your HTML source code here..."
        spellcheck="false"
    ></textarea>

    <div id="selectionBar">
        <strong>Selected text</strong>

        <span id="selectedText"></span>

        <button class="primary" id="addButton">
            Add
        </button>

        <button id="cancelButton">
            Cancel
        </button>
    </div>

</div>

<!-- XPath options -->
<div id="options">
    <h2>XPath Options</h2>

    <div id="optionPanel">

        <label>
            <input type="checkbox" id="o_text" checked>
            Add //text()
        </label>

        <label>
            <input type="checkbox" id="o_anchor" checked>
            Include parent anchor
        </label>

        <label>
            <input type="checkbox" id="o_exact">
            Exact text match
        </label>

        <label>
            <input type="checkbox" id="o_label" checked>
            Use label words
        </label>

        <label>
            <input type="checkbox" id="o_index">
            Allow position numbers
        </label>

        <label>
            XPaths per text
            <select id="o_limit">
                <option value="1">Best 1</option>
                <option value="2" selected>Best 2</option>
                <option value="5">Best 5</option>
                <option value="0">All</option>
            </select>
        </label>

    </div>
</div>

<!-- Selected texts -->
<div class="bar">
    <strong>Selected Texts</strong>

    <span class="muted" id="itemCount">0 items</span>

    <span class="grow"></span>

    <button id="generateButton" class="primary" disabled>
        Generate XPath
    </button>
</div>

<div id="tray">
    <span class="muted">
        Select text in the HTML editor and click Add.
    </span>
</div>

<!-- Results -->
<div class="bar">
    <strong>XPath Results</strong>

    <span class="grow"></span>

    <button id="copyAllButton" disabled>
        Copy All JSON
    </button>
</div>

<div id="results"></div>

<script>
const $ = id => document.getElementById(id);

let items = [];
let pendingText = "";
let lastResults = null;


// --------------------------------------------------
// Dark Mode / Light Mode
// --------------------------------------------------
$("themeButton").onclick = function () {
    document.body.classList.toggle("dark-mode");

    const isDark = document.body.classList.contains("dark-mode");

    $("themeButton").textContent = isDark
        ? "Light Mode"
        : "Dark Mode";
};


// --------------------------------------------------
// Selected source -> plain text
// (removes tags / comments / entities, e.g. "₹<!-- -->139" -> "₹139")
// --------------------------------------------------
function toText(raw) {
    if (/[<&]/.test(raw)) {
        try {
            raw = new DOMParser()
                .parseFromString("<body>" + raw + "</body>", "text/html")
                .body.textContent;
        } catch (error) {}
    }

    return raw.replace(/\s+/g, " ").trim();
}


// --------------------------------------------------
// Select text from the HTML source
// --------------------------------------------------
function showSelection() {
    const editor = $("html");

    const start = editor.selectionStart;
    const end = editor.selectionEnd;

    if (start === end) {
        return;
    }

    const selected = toText(editor.value.slice(start, end));

    if (!selected) {
        return;
    }

    pendingText = selected;

    $("selectedText").textContent = pendingText;
    $("selectionBar").style.display = "block";

    $("addButton").textContent = items.includes(pendingText)
        ? "Already Added"
        : "Add";
}

$("html").addEventListener("mouseup", showSelection);
$("html").addEventListener("keyup", showSelection);


// --------------------------------------------------
// Add selected text
// --------------------------------------------------
$("addButton").onclick = function () {
    if (!pendingText) {
        return;
    }

    if (items.includes(pendingText)) {
        pendingText = "";
        $("selectionBar").style.display = "none";
        return;
    }

    items.push(pendingText);

    pendingText = "";
    $("selectionBar").style.display = "none";

    renderTray();
};

$("cancelButton").onclick = function () {
    pendingText = "";
    $("selectionBar").style.display = "none";
};


// --------------------------------------------------
// Display selected texts
// --------------------------------------------------
function renderTray() {
    const tray = $("tray");
    tray.textContent = "";

    if (!items.length) {
        const message = document.createElement("span");
        message.className = "muted";
        message.textContent =
            "Select text in the HTML editor and click Add.";

        tray.appendChild(message);
    }

    items.forEach((text, index) => {
        const chip = document.createElement("div");
        chip.className = "chip";

        const label = document.createElement("span");
        label.textContent = text;

        const remove = document.createElement("button");
        remove.textContent = "×";
        remove.title = "Remove selected text";

        remove.onclick = function () {
            items.splice(index, 1);
            renderTray();
        };

        chip.append(label, remove);
        tray.appendChild(chip);
    });

    $("itemCount").textContent =
        items.length + (items.length === 1 ? " item" : " items");

    $("generateButton").disabled = items.length === 0;
}


// --------------------------------------------------
// Generate XPath
// --------------------------------------------------
$("generateButton").onclick = async function () {
    const htmlText = $("html").value;

    if (!htmlText.trim()) {
        alert("Please paste HTML source code first.");
        return;
    }

    if (!items.length) {
        alert("Select and add some text first.");
        return;
    }

    const button = $("generateButton");

    button.disabled = true;
    button.textContent = "Working...";

    try {
        const response = await fetch("/api/xpath", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                html: htmlText,

                items: items.map(text => ({
                    text: text
                })),

                opts: {
                    with_text: $("o_text").checked,
                    anchor: $("o_anchor").checked,
                    exact: $("o_exact").checked,
                    use_label: $("o_label").checked,
                    index: $("o_index").checked,
                    limit: Number($("o_limit").value)
                }
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Server error");
        }

        lastResults = data.results;
        renderResults();

    } catch (error) {
        alert("Error: " + error.message);

    } finally {
        button.disabled = items.length === 0;
        button.textContent = "Generate XPath";
    }
};


// --------------------------------------------------
// Display XPath results
// --------------------------------------------------
function renderResults() {
    const box = $("results");
    box.textContent = "";

    items.forEach((text, index) => {
        const card = document.createElement("div");
        card.className = "result";

        const heading = document.createElement("h3");
        heading.textContent = text;
        card.appendChild(heading);

        const list = (lastResults || {})[String(index)] || [];

        const summary = document.createElement("div");
        summary.className = "muted";
        summary.textContent = list.length
            ? list.length + " XPath result(s), best first"
            : "No XPath found for this text.";

        card.appendChild(summary);

        list.forEach(result => {
            const xpathBox = document.createElement("div");
            xpathBox.className = "xpath-box";

            const code = document.createElement("code");
            code.className = "xpath-code";
            code.textContent = result.xpath;

            const meta = document.createElement("div");
            meta.className = "meta";

            const copyButton = document.createElement("button");
            copyButton.textContent = "Copy XPath";

            copyButton.onclick = async function () {
                try {
                    await navigator.clipboard.writeText(result.xpath);
                    copyButton.textContent = "Copied";
                } catch (error) {
                    alert("Unable to copy XPath. Please copy it manually.");
                }
            };

            const status = document.createElement("span");
            status.className = result.error ? "bad" : "ok";

            status.textContent = result.error
                ? "Invalid XPath: " + result.error
                : result.count + " node(s) matched";

            meta.append(copyButton, status);
            xpathBox.append(code, meta);

            if (result.values && result.values.length) {
                const values = document.createElement("div");
                values.className = "values";
                values.textContent =
                    "Returns: " + result.values.join(" | ");

                xpathBox.appendChild(values);
            }

            card.appendChild(xpathBox);
        });

        box.appendChild(card);
    });

    $("copyAllButton").disabled = !lastResults ||
        !Object.keys(lastResults).length;
}


// --------------------------------------------------
// Copy best XPath results as JSON
// --------------------------------------------------
$("copyAllButton").onclick = async function () {
    const output = {};

    items.forEach((text, index) => {
        const list = (lastResults || {})[String(index)] || [];

        if (list.length) {
            output[text] = list[0].xpath;
        }
    });

    try {
        await navigator.clipboard.writeText(
            JSON.stringify(output, null, 2)
        );

        $("copyAllButton").textContent = "Copied JSON";

    } catch (error) {
        alert("Unable to copy JSON.");
    }
};


// --------------------------------------------------
// Clear all
// --------------------------------------------------
$("clearButton").onclick = function () {
    $("html").value = "";
    $("results").textContent = "";

    items = [];
    pendingText = "";
    lastResults = null;

    $("selectionBar").style.display = "none";
    $("copyAllButton").disabled = true;

    $("copyAllButton").textContent = "Copy All JSON";

    renderTray();
};

</script>

</body>
</html>
"""


# --------------------------------------------------
# HTTP Server
# --------------------------------------------------
class Handler(BaseHTTPRequestHandler):

    def _send(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(
                200,
                PAGE.encode("utf-8"),
                "text/html; charset=utf-8"
            )
        elif self.path == "/health":          # used by hosting platforms
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/xpath":
            self._send(404, b"Not found", "text/plain")
            return

        try:
            size = int(self.headers.get("Content-Length", 0))

            if size > MAX_BODY:
                # read (and drop) the upload so the browser can show our message
                left = min(size, 50 * 1024 * 1024)
                while left > 0:
                    chunk = self.rfile.read(min(65536, left))
                    if not chunk:
                        break
                    left -= len(chunk)

                self._send(
                    413,
                    json.dumps(
                        {"error": "HTML is too large (max 5 MB)."}
                    ).encode("utf-8"),
                    "application/json; charset=utf-8"
                )
                return

            payload = json.loads(
                self.rfile.read(size).decode("utf-8")
            )

            results = generate(
                payload.get("html", ""),
                payload.get("items", []),
                payload.get("opts", {})
            )

            body = json.dumps(
                {"results": results},
                ensure_ascii=False
            ).encode("utf-8")

            self._send(
                200,
                body,
                "application/json; charset=utf-8"
            )

        except Exception as error:
            body = json.dumps(
                {"error": str(error)},
                ensure_ascii=False
            ).encode("utf-8")

            self._send(
                400,
                body,
                "application/json; charset=utf-8"
            )

    def log_message(self, *args):
        pass


# --------------------------------------------------
# Start server
# --------------------------------------------------
def main():
    port = PORT

    while True:
        try:
            server = ThreadingHTTPServer((HOST, port), Handler)
            break
        except OSError:
            if HOSTED:
                raise          # on a host the port must be exactly the one given
            port += 1          # locally: port busy -> try the next one

    shown = "127.0.0.1" if HOST in ("0.0.0.0", "127.0.0.1") else HOST
    url = f"http://{shown}:{port}"

    print(f"XPath Maker running at {url}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    if not HOSTED:                 # don't try to open a browser on a server
        threading.Timer(
            0.8,
            lambda: webbrowser.open(url)
        ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
