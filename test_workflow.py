"""Structure checks + Code-node logic in Node.js (RSS fetch is stubbed, no network)."""
import json, shutil, subprocess, unittest
from pathlib import Path
import generate_workflow as gw

HERE = Path(__file__).parent
WF = gw.workflow
NODE = shutil.which("node")

RSS = """<rss><channel>
<item><title>Big AI story - Example News</title><link>https://ex.com/1</link><pubDate>{now}</pubDate><description>&lt;b&gt;Short&lt;/b&gt; text</description><source>Example News</source></item>
<item><title>Big AI story - Other Site</title><link>https://ex.com/2</link><pubDate>{now}</pubDate><description>dup</description></item>
<item><title>Old story</title><link>https://ex.com/3</link><pubDate>Mon, 01 Jan 2001 00:00:00 GMT</pubDate></item>
</channel></rss>"""


def run_js(code, items, refs=None, http=""):
    harness = f"""
const items = {json.dumps(items)}; const refs = {json.dumps(refs or {})};
const $input = {{ all: () => items, first: () => items[0] }};
const $ = (n) => ({{ all: () => refs[n], first: () => refs[n][0] }});
const $now = {{ setZone: () => ({{ toFormat: () => '01.01.2026' }}) }};
const ctx = {{ helpers: {{ httpRequest: async () => {json.dumps(http)}.replace(/{{now}}/g, new Date().toUTCString()) }} }};
(async function () {{ {code} }}).call(ctx).then(r => console.log(JSON.stringify(r)));
"""
    res = subprocess.run([NODE, "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=30)
    if res.returncode:
        raise AssertionError(res.stderr)
    return json.loads(res.stdout)


class TestStructure(unittest.TestCase):
    def test_file_matches_generator(self):
        self.assertEqual(json.loads((HERE / "AI_News_Desk.json").read_text(encoding="utf-8")), json.loads(json.dumps(WF)))

    def test_connections(self):
        names = {n["name"] for n in WF["nodes"]}
        for src, out in WF["connections"].items():
            self.assertIn(src, names)
            for br in out["main"]:
                for c in br:
                    self.assertIn(c["node"], names)

    def test_no_secrets(self):
        self.assertNotRegex((HERE / "AI_News_Desk.json").read_text(encoding="utf-8"), r"AIza[0-9A-Za-z_\-]{30,}|\b\d{17,20}\b|\$env\.")


@unittest.skipUnless(NODE, "Node.js not installed")
class TestLogic(unittest.TestCase):
    def test_topic_request_clamps_days(self):
        r = run_js(gw.TOPIC_JS, [{"json": {"body": {"topic": "n8n", "days": 99}}}])[0]["json"]
        self.assertEqual(r["days"], 30)
        self.assertIn("when%3A30d", r["feeds"][0]["url"])

    def test_fetch_filters_old_and_duplicates(self):
        req = run_js(gw.TOPIC_JS, [{"json": {}}])
        r = run_js(gw.FETCH_JS, req, http=RSS)[0]["json"]
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["items"][0]["desc"], "Short text")
        self.assertIn("[1]", r["requestBody"]["contents"][0]["parts"][0]["text"])

    def test_format_splits_for_discord(self):
        req = run_js(gw.TOPIC_JS, [{"json": {}}])
        fetched = run_js(gw.FETCH_JS, req, http=RSS)
        long_text = "word " * 900
        out = run_js(gw.FORMAT_JS, [{"json": {"candidates": [{"content": {"parts": [{"text": long_text}]}}]}}], {"Fetch & Filter Feeds": fetched})
        self.assertGreater(len(out), 1)
        self.assertTrue(all(len(o["json"]["content"]) <= 1900 for o in out))
        self.assertIn("**Sources**", out[0]["json"]["markdown"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
