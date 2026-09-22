/**
 * agent-bus for omp: reads its own mail, without spending a model turn on it.
 *
 * Every MCP `notifications/resources/updated` for the inbox used to cost a
 * full model turn just to notice it, fetch the inbox, read the message, and
 * ack it -- four tool calls and a round trip of tokens for something
 * mechanical. `mcp_notification` (omp's own event, fired for every
 * notification a connected MCP server sends) lets this extension do that
 * work in TypeScript instead: fetch, inject as a steer, ack -- zero model
 * turns spent on the plumbing.
 *
 * Whether this omp session is on the bus at all is decided entirely by its
 * own MCP config, not by this extension: agent-bus registers on connect
 * only when `AGENT_BUS_NAME` is set in the `agent-bus` server's own `env`,
 * so a project that never sets it stays off the bus regardless of whether
 * this extension is loaded.
 *
 * Install: symlink or copy this file into `~/.omp/agent/extensions/` (every
 * project) or `<project>/.omp/extensions/` (one project only), and add
 * agent-bus as an MCP server with a name for this project:
 *
 *   { "mcpServers": { "agent-bus": {
 *       "command": "agent-bus", "args": ["mcp"],
 *       "env": { "AGENT_BUS_NAME": "labkit-dev" }
 *   } } }
 */

import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const AGENT_BUS_SERVER = "agent-bus";
const INBOX_URI = "agentbus://inbox";

interface InboxMessage {
  id: string;
  from: { name: string; kind: string };
  summary: string;
  text: string;
}

export default function agentBusExtension(pi: ExtensionAPI) {
  pi.setLabel("agent-bus");

  // The one signal that this extension is loaded at all: it otherwise reacts
  // only to a push nobody controls the timing of, so a session that never
  // receives mail would write nothing and "did this load" would stay
  // unanswerable. Written to omp's own log
  // (~/.omp/logs/omp.<date>.<pid>.log), the same place every other
  // omp-internal record goes -- `pi.logger` is that logger, not a separate one.
  pi.logger.info("agent-bus extension loaded");

  // The whole point: react to the push, do the fetch/inject/ack mechanically,
  // spend zero model turns on it. agent-bus's own notification carries only
  // the URI, never content (deliberate -- "notice, not body") so this still
  // needs the follow-up fetch; the saving is the four tool calls an LLM
  // would otherwise spend noticing, fetching, reading, and acking one at a
  // time.
  pi.on("mcp_notification", async (event, ctx) => {
    if (event.server !== AGENT_BUS_SERVER) return;
    if (event.method !== "notifications/resources/updated") return;
    const params = event.params as { uri?: string } | null;
    if (params?.uri !== INBOX_URI) return;

    // No --target: this connection's own identity is resolved the same way
    // `register` and `self` resolve it -- by walking the process tree from
    // this CLI child back to omp's own long-lived pid, the direct parent of
    // whatever pi.exec spawns. That is the same pid AGENT_BUS_NAME
    // registered under at startup, so the implicit resolution and the
    // explicit one agree.
    const listed = await pi.exec("agent-bus", ["inbox", "--unread", "--json"], { cwd: ctx.cwd });
    if (listed.code !== 0) {
      pi.logger.warn("agent-bus: could not read inbox", { stderr: listed.stderr });
      ctx.ui.notify(`agent-bus: could not read inbox: ${listed.stderr}`, "warning");
      return;
    }
    let messages: InboxMessage[];
    try {
      messages = JSON.parse(listed.stdout);
    } catch {
      return;
    }
    pi.logger.info("agent-bus: unread messages fetched", { count: messages.length });

    for (const msg of messages) {
      const full = await pi.exec("agent-bus", ["read", msg.id, "--json"], { cwd: ctx.cwd });
      let text = msg.text;
      if (full.code === 0) {
        try {
          text = (JSON.parse(full.stdout) as InboxMessage).text ?? text;
        } catch {
          // fall back to the summary-shaped notice already in hand
        }
      }
      await pi.exec("agent-bus", ["ack", msg.id], { cwd: ctx.cwd });
      const subject = msg.summary ? ` (${msg.summary})` : "";
      // Already fetched and acked above -- said explicitly, not left
      // implicit, because omp's own runtime separately renders the raw
      // notification event into this same session regardless of this
      // handler (docs/extensions.md's own mcp_notification section: the
      // handler runs "AFTER the manager's own handling"). Confirmed live:
      // without this line, the model saw both and independently called
      // self/get_inbox/ack_message again on its own initiative -- the
      // exact four-tool-call cost this extension exists to remove -- even
      // though the ack had already landed before the model's turn started.
      pi.sendUserMessage(
        `[agent-bus message from ${msg.from.name}, already fetched and acked -- informational only]${subject}\n${text}`,
        { deliverAs: "steer" },
      );
    }
  });
}
