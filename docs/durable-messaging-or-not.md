# Durable Messaging... or not

## There are two Special Cases of Coding Harness
(as of **24 August 2026**)

### There's Claude Code....
- The `agent-bus` project spawned as a drop-in adapter for non-Claude coding harnesses that Just Works™.
- Claude Code users see agent-bus agents through the `/ListAgents` slash-commmand.
- Claude the AI agnet sees the same Roster through its native `ListAgents` tool, it sends messages to Claude Code peers and `agent-bus` peers through its - `SendMessage` tool.
- Claude Code's [Cross-Session Messaging](https://code.claude.com/docs/en/agent-teams) handles the delivery both ways, as long as we conform to the UDP protocol.
- Claude Code spawns a process per Agent session, each process announces a UDP socket linked to a PID.
- Claude Code Messaging is either "you may deliver it now, or not now."
- Claude Code Messaging is either "you may deliver it now, or not now."

### There's Codex
- Codex implements its own persistent-mailbox-backed discovery and messaging.
- We read from it, we do not announce agent-bus peers by writing into their SQLite databaase.
- Codex's partition seam is ThreadId, not Process PID.
- Codex Messaging writes-through their persistent SQLite-backed durable messaging.
- Write-now, it may be read later. (TODO: correct-if-necessary)

### ...and and there's Everyone Else
- `pi` uses naked `agent-bus` CLI calls: `agent-bus listen` is required for **both** sending and receiving from Claude Code peers. OOTB Pi has no MCP support, no hooks, only a handful of native tools. That's all it needs. Pi agents will always show up on `list-agents` as `kind: other` - that's a feature, not a bug.
- `oh-my-pi` gives its agents a nodejs and python runtime, so, if you wanted to, you could just instruct `omp` to use `agent-bus` as a library and modify it to bolt on whatever features are desired - as long as it behaves itself. Most users will likely opt for the MPC server implementation. We've figured out the discoverability adapter for `omp` agents. Users just need to wire in the mcp server config, if `omp` hasn't already imported it from other coding harnesses where it's already wired up.
- `grok` has a half-implemented discoverability story: some Grok Build sessions advertise a leader socket, from this we can subscribe to `x.ai/sessions/changed` events to keep the `list-agents` view live and up-to-date. In testing on the maintainer's machine, we have not yet seen any `grok` sessions which have advertised a leader socket address. Hypothesis: this is Grok's IPC mechanism for leader-subagent messaging, not agent-peer messaging. Therefore, `grok` straddles a grey area somewhere between discoverable-and-not-discoverable.
- `oh-my-opencode` is installed on the maintainer's machine but until and unless anybody asks for it, `omo` compatibility is not on the TODO-list. If `omo` suits your needs, `omo` is the **only** coding harness you need. `agent-bus` is for when you want a hybrid chimera of agents running in multiple harnesses.

---

## There are two Special Cases of AI Chat Apps
In this section of the note, we'll refer to `agent-bus mail` instead of naming its predecessor.
At time of writing this is a feature that does not yet exist.
But it did, and it worked.
Hence `agent-bus`.

### There's Claude...
- There's two flavours of Claude in this domain context. Claude running in the Claude Code harness, and Claude running in Claude Desktop, or on the web in `claude.ai`. They're actually both the same thing, Claude Desktop **is** `claude.ai` running in an electron shell, on your desktop.
- Claude Desktop supports MCP servers over stdio and HTTPS transport. Well, it does and it doesn't. sdio MCP servers running on your machine are available to Claude when you're on your desktop. You can continue the same chat on your phone, but it won't have access to those MCP servers. You can wire up an MCP server over HTTPs, but it **must be a publicly accessible domain** which is properly configured for HTTPS. These MCP servers are accessible from your desktop, or on-the-go on your mobile.
- A prior incarnation of `agent-bus` was a durable file-based inbox exposed through an MCP server. This was exposed to the public internet through an HTTPS reverse-proxy + local SSH tunnel. This is **the only way** to get Claude Desktop to talk to Claude Code when they're both running on the same machine... your desktop.
- Claude Desktop has no "wake up". There's no `loop`. There's no "insert a messsage into Claude's context when it's finished it's turn". The user has to prod Claude "you've got mail", "check your agent-bus inbox", "send an agent-bus mail to Claude Code instance `agent-bus-dev`", "send an agent-bus mail to ChatGPT".
- This actually works fine, because **the Claude conversations you're keeping in your pocket tend to be long-context strategic advisors**, and you probably have sore thumbs from copy and pasting context between those chats and coding agent chats.

### ...and then there's ChatGPT.... or rather... ChatGPT, and Not-Chat-But-Work-GPT. There's also Codex... which is not Codex but Codex-wearing-Work-GPT clothing. It's Work-GPT but not Work-GPT. It's Codex under the hood. Without the freedom. More freedom than ChatGPT. It lives in the ChatGPT app. Except it doesn't.
- For simplicity we'll refer to this marketing-driven-architecture mess as "ChatGPT".
- It's an AI chat, that you access from your phone, and sometimes your desktop. The desktop app can switch to "Work" mode and control your computer. But then it dumps all the context from ChatGPT. There's also Codex, which is basically what all this is under the hood, except for when it isn't. Right, we'll just call it ChatGPT.
- Think of it like Claude Desktop. It can do stuff with you're computer when you're at your computer. When you're not there, you need a publicly accessible HTTPS MCP server. But don't think you can just deploy updates and test them in ChatGPT. OpenAI seem to be doing verification checks and WAF-like message filtering. If they think your traffic looks odd, it gets dropped silently. This is completely opaque and impossible to debug. Just be grateful it works - sometimes.
- If you're still reading this, **the ChatGPT conversations you're keeping in your pocket tend to be long-context strategic advisors**, and you probably have sore thumbs from copy and pasting context between those chats and coding agent chats.

### Why the Dinosaurs still Matter
- Both Claude (not Claude Code) and ChatGPT (not WorkGPT) **can** pull code from GitHub into their ephemeral sandbox environments.
- They can do code reviews.
- They can even edit code - but getting code back out of them involves copy and paste
- **They are still useful**
- Even when the user has to wake them by saying "you've got mail"
- and "agent-bus mail seems to be down, I'll copy and paste it instead"
- Whatever. You're still here. You're either using `agent-bus`, building `agent-bus`, or worse, thinking of extending it for even more convoluted Rube Goldberg machines.

---

## `agent-bus mail` - or `agent-bus cloud-inbox`, or `agent-bus cloud-listen`

**TL;DR:** There's two ways of going about this:

How it was done before
- a localhost http MCP server, same functions as `agent-bus`, or just a new type of `listen` in `agent-bus` CLI
- user is authed via OAuth
- publicly accessible HTTPS reverse-proxy routes traffic to localhost through flakey SSH tunnel
- OpenAI silently breaks everything every time you ship-and-test a new version
- Everything Just Works™ for Claude Desktop, so Claude Desktop and Claude Code keep on sending mail like a well-oiled office team while the user fantically copy and pastes context to and from ChatGPT on their phone

How it could be done next:
- A new `kind` of peer: `desktop`
- `desktop` means it's accessible on your desktop, but also on the go, through the web or a web app wrapped in a native app container
- We **have to** route traffic out and back in through the public internet for IPC **on the same machine**
- We **have to** 'wake' the agent through manual user prompt invocation "You've got mail"
- `desktop:Claude` and `desktop:ChatGPT` are **not** IPC peers, even though their harnesses may sometimes be running in apps on the same machine
- The same paradigm can extend to Claude Code agents running in the cloud, subject to sandbox restrictions + permissions
- This is the ancestory of the file-backed persistent inbox. This is the **only** way to for a desktop-class peer to send and receive messages to coding-harness peer regaredless of whether the current conversation turn is happening on the same computer or in the cloud.
- **Persistence is not a feature**. Persistence is a necessity for **everyone except Claude Code and Codex**. And Codex implements persistence under-the-hood. That's why it's not strictly a necessity for `agent-bus` inbound to Codex, but **is** necessary for Codex outbound through `agent-bus`.
- The idea: two classes of inbox: file-based and cloud-based.
- `agent-bus listen-cloud` reads and writes from a cloud persistent store. or message bus. Whatever. It's not on our machine. `desktop` kind peers can only read and write through a public HTTPS MCP server, and only upon user intervention.
- `agent-bus listen-cloud` **can** use the UDP protocol for "message now or not right now" exchanges with Claude Code. "Message queued to Claude Desktop's inbox. You'll be notified when they reply. This requires an explicit instruction from the User."
- `agent-bus listen-cloud` **can** advertise itself as a local peer. It just acts as a broker between local file-based durable message inbox and durable cloud-based message outbox. Messages received go to Claude Code now... or they can wait in a local inbox for the user to tell a Claude Code agent to read from their `agent-bus inbox` through the MCP server. Whatever - every other agent is going to be told one way or another, through `agent-bus watch` or through user instruction, to read their `agent-bus inbox`.
- `agent-bus-gcloud`: doesn't exist. Might exist in the next day or two. Publicly available HTTPS MCP server accessible to Claude Desktop and ChatGPT Desktop. Claude or ChatGPT in your pocket. Whatever. They need the Cloud. What you need is: something that doesn't bill when it isn't being used. Something that's easily testable and disposable (can be spun up/torn down through Terraform.) Something that's repeatable and testable (can be spun up/torn down through Terraform.) Something that doesn't involve running an SSH tunnel alongside a nodejs MCP server whicn you're both **working on** and **relying on** at **the same time**.