"""Builds AI_News_Desk.json: (1) on-demand topic briefing from Google News RSS for a chosen time window,
(2) a daily 'newspaper' from your own RSS feeds posted to Discord. Gemini writes the briefing from the fetched items only."""
import json
from pathlib import Path

HERE = Path(__file__).parent

TOPIC_JS = r"""// On-demand request: topic + time window (days). Webhook body or manual defaults.
const b = $input.first().json.body || $input.first().json || {};
const topic = String(b.topic || 'AI agents in business automation').trim();
const days = Math.min(Math.max(parseInt(b.days || 3, 10) || 3, 1), 30);
const language = String(b.language || 'English');
const q = encodeURIComponent(`${topic} when:${days}d`);
return [{ json: { mode: 'topic', title: `${topic} — last ${days} day(s)`, days, language, maxItems: Math.min(parseInt(b.max || 15, 10) || 15, 40),
  feeds: [{ section: topic, url: `https://news.google.com/rss/search?q=${q}&hl=en-US&gl=US&ceid=US:en` }] } }];"""

PAPER_JS = r"""// Daily newspaper: edit your sections and feeds here.
const feeds = [
  { section: 'Security', url: 'https://feeds.feedburner.com/TheHackersNews' },
  { section: 'Security', url: 'https://www.bleepingcomputer.com/feed/' },
  { section: 'AI & Automation', url: 'https://news.google.com/rss/search?q=%22AI+agents%22+OR+n8n+OR+automation+when:1d&hl=en-US&gl=US&ceid=US:en' },
  { section: 'Tech', url: 'https://hnrss.org/frontpage?points=150' },
];
return [{ json: { mode: 'daily', title: 'Morning newspaper — ' + $now.setZone('Europe/Warsaw').toFormat('dd.MM.yyyy'), days: 1, language: 'English', maxItems: 30, feeds } }];"""

FETCH_JS = r"""// Fetch every RSS/Atom feed, keep items inside the time window, remove duplicates by title.
const req = $input.first().json;
const since = Date.now() - req.days * 86400000;
const dec = s => String(s || '').replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
const tag = (x, t) => { const m = x.match(new RegExp(`<${t}[^>]*>([\\s\\S]*?)<\\/${t}>`, 'i')); return m ? dec(m[1]).trim() : ''; };
const items = [], errors = [], seen = new Set();
for (const f of req.feeds) {
  let xml = '';
  try { xml = await this.helpers.httpRequest({ url: f.url, headers: { 'User-Agent': 'Mozilla/5.0 (n8n News Desk)' }, json: false, timeout: 20000 }); }
  catch (e) { errors.push(`${f.url.slice(0, 60)}: ${String(e.message || e).slice(0, 80)}`); continue; }
  const blocks = String(xml).split(/<item[\s>]|<entry[\s>]/i).slice(1);
  for (const b of blocks) {
    const title = tag(b, 'title').replace(/<[^>]+>/g, '');
    const link = tag(b, 'link') || (b.match(/<link[^>]+href="([^"]+)"/i) || [])[1] || '';
    const date = new Date(tag(b, 'pubDate') || tag(b, 'published') || tag(b, 'updated') || Date.now());
    const key = title.toLowerCase().replace(/\s+-\s+[^-]+$/, '').replace(/[^a-z0-9 ]/g, '').trim();
    if (!title || seen.has(key) || (!isNaN(date) && date.getTime() < since)) continue;
    seen.add(key);
    const desc = (tag(b, 'description') || tag(b, 'summary')).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 300);
    items.push({ section: f.section, title, link, source: tag(b, 'source') || (link.match(/https?:\/\/([^/]+)/) || [])[1] || '', date: isNaN(date) ? '' : date.toISOString(), desc });
  }
}
items.sort((a, b) => (b.date || '').localeCompare(a.date || ''));
const picked = items.slice(0, req.maxItems);
const list = picked.map((it, i) => `[${i + 1}] (${it.section}) ${it.title} — ${it.source} ${it.date.slice(0, 10)}\n${it.desc}`).join('\n\n');
const prompt = `You are a news editor. Write a ${req.mode === 'daily' ? 'morning newspaper' : 'news briefing'} in ${req.language} titled "${req.title}".
Use ONLY the items below; do not add facts. Group by section, start with the 3 most important stories, give each story a bold headline and 1-2 sentence summary, and cite sources as [n].
End with "Why it matters" (2 bullet points). Keep it under 450 words. Markdown only.

${list || '(no items)'}`;
return [{ json: { ...req, items: picked, errors, count: picked.length,
  requestBody: { contents: [{ parts: [{ text: prompt }] }], generationConfig: { temperature: 0.3 } } } }];"""

FORMAT_JS = r"""// Attach the numbered source list and split for Discord (<= 1900 chars per message).
const req = $('Fetch & Filter Feeds').first().json;
const text = $input.first().json.candidates?.[0]?.content?.parts?.[0]?.text || (req.count ? '_The model returned no text._' : '_No news items in this time window._');
const sources = req.items.map((it, i) => `[${i + 1}] ${it.title} — <${it.link}>`).join('\n');
const body = text.replace(/^\s*#{1,2} .*\n+/, '');   // the model often repeats the title as a heading
const markdown = `## ${req.title}\n\n${body}\n\n**Sources**\n${sources}` + (req.errors.length ? `\n\n_Feeds with errors: ${req.errors.length}_` : '');
const lines = markdown.split('\n').flatMap(l => l.length > 1900 ? l.match(/[\s\S]{1,1900}/g) : [l]);  // hard-split very long lines
const chunks = []; let cur = '';
for (const line of lines) { if ((cur + '\n' + line).length > 1900) { chunks.push(cur); cur = line; } else cur = cur ? cur + '\n' + line : line; }
if (cur) chunks.push(cur);
return chunks.map((content, i) => ({ json: { mode: req.mode, title: req.title, count: req.count, markdown: i === 0 ? markdown : undefined, content } }));"""

DISCORD = {"authentication": "oAuth2", "resource": "message",
           "guildId": {"__rl": True, "value": "YOUR_DISCORD_SERVER_ID", "mode": "id"},
           "channelId": {"__rl": True, "value": "YOUR_DISCORD_CHANNEL_ID", "mode": "id"}, "options": {}}


def node(name, typ, ver, pos, params, **extra):
    n = {"parameters": params, "name": name, "type": typ, "typeVersion": ver, "position": pos}
    n.update(extra)
    return n


nodes = [
    node("Manual Test", "n8n-nodes-base.manualTrigger", 1, [-700, -120], {}),
    node("Webhook (POST /news-desk)", "n8n-nodes-base.webhook", 2, [-700, 60],
         {"httpMethod": "POST", "path": "news-desk", "responseMode": "responseNode", "options": {}}, webhookId="news-desk"),
    node("Every Morning 07:00", "n8n-nodes-base.scheduleTrigger", 1.2, [-700, 280],
         {"rule": {"interval": [{"field": "cronExpression", "expression": "0 7 * * *"}]}}),
    node("Topic Request", "n8n-nodes-base.code", 2, [-460, -40], {"jsCode": TOPIC_JS}),
    node("Newspaper Feeds", "n8n-nodes-base.code", 2, [-460, 280], {"jsCode": PAPER_JS}),
    node("Fetch & Filter Feeds", "n8n-nodes-base.code", 2, [-220, 100], {"jsCode": FETCH_JS}),
    node("Gemini Editor", "n8n-nodes-base.httpRequest", 4.2, [0, 100], {
        "method": "POST", "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        "authentication": "predefinedCredentialType", "nodeCredentialType": "googlePalmApi",
        "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.requestBody) }}", "options": {"timeout": 90000}},
        credentials={"googlePalmApi": {"id": "", "name": "Gemini API"}}, retryOnFail=True, maxTries=3, waitBetweenTries=5000),
    node("Format Briefing", "n8n-nodes-base.code", 2, [220, 100], {"jsCode": FORMAT_JS}),
    node("Daily?", "n8n-nodes-base.if", 2.2, [440, 100], {"conditions": {"options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
        "conditions": [{"leftValue": "={{ $json.mode }}", "rightValue": "daily", "operator": {"type": "string", "operation": "equals"}}],
        "combinator": "and"}, "options": {}}),
    node("Discord: Newspaper", "n8n-nodes-base.discord", 2, [680, 20], {**DISCORD, "content": "={{ $json.content }}"},
         credentials={"discordOAuth2Api": {"id": "", "name": "Discord Bot"}}, retryOnFail=True, maxTries=3, waitBetweenTries=5000),
    node("Respond with Briefing", "n8n-nodes-base.respondToWebhook", 1.1, [680, 200],
         {"respondWith": "json", "responseBody": "={{ JSON.stringify({ title: $json.title, count: $json.count, markdown: $('Format Briefing').first().json.markdown }) }}", "options": {}},
         executeOnce=True),
]
C = lambda *t: {"main": [[{"node": x, "type": "main", "index": 0} for x in t]]}
conns = {"Manual Test": C("Topic Request"), "Webhook (POST /news-desk)": C("Topic Request"), "Every Morning 07:00": C("Newspaper Feeds"),
         "Topic Request": C("Fetch & Filter Feeds"), "Newspaper Feeds": C("Fetch & Filter Feeds"), "Fetch & Filter Feeds": C("Gemini Editor"),
         "Gemini Editor": C("Format Briefing"), "Format Briefing": C("Daily?"),
         "Daily?": {"main": [[{"node": "Discord: Newspaper", "type": "main", "index": 0}], [{"node": "Respond with Briefing", "type": "main", "index": 0}]]}}
workflow = {"name": "AI News Desk", "nodes": nodes, "connections": conns, "settings": {"executionOrder": "v1"}}

if __name__ == "__main__":
    out = HERE / "AI_News_Desk.json"
    out.write_text(json.dumps(workflow, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}")
