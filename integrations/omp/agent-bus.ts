/**
 * agent-bus for omp: passive by default, and reads its own mail.
 *
 * Two problems this closes, both found live:
 *
 * 1. Every MCP `notifications/resources/updated` for the inbox used to cost
 *    a full model turn just to notice it, fetch the inbox, read the message,
 *    and ack it -- four tool calls and a round trip of tokens for something
 *    mechanical. `mcp_notification` (omp's own event, fired for every
 *    notification a connected MCP server sends) lets this extension do that
 *    work in TypeScript instead: fetch, inject as a steer, ack -- zero model
 *    turns spent on the plumbing.
 *
 * 2. Every omp session that has agent-bus configured as an MCP server used
 *    to register itself and start a listener the moment it connected, merely
 *    by existing -- an omp user who never asked to participate in agent-bus
 *    collaboration got a roster entry and a socket anyway. This extension
 *    stays fully passive until `/agent-bus-join` is run once per project;
 *    from then on it re-registers silently on every future session_start in
 *    that project, so joining is a one-time action, not a per-session one.
 *    The agent-bus side of this is `AGENT_BUS_NO_AUTO_REGISTER=1` in the
 *    server's own `env` below -- without it, agent-bus registers on connect
 *    regardless of anything this extension does.
 *
 * Install: symlink or copy this file into `~/.omp/agent/extensions/` (every
 * project) or `<project>/.omp/extensions/` (one project only), and add
 * agent-bus as an MCP server with AGENT_BUS_NO_AUTO_REGISTER set:
 *
 *   { "mcpServers": { "agent-bus": {
 *       "command": "agent-bus", "args": ["mcp"],
 *       "env": { "AGENT_BUS_NO_AUTO_REGISTER": "1" }
 *   } } }
 *
 * Then run `/agent-bus-join` once in each project you want on the bus.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";

const AGENT_BUS_SERVER = "agent-bus";
const AGENT_BUS_KIND = "omp";
const INBOX_URI = "agentbus://inbox";
const STATE_DIR = ".omp";
const STATE_FILE = "agent-bus.json";

interface JoinState {
  name: string;
}

interface InboxMessage {
  id: string;
  from: { name: string; kind: string };
  summary: string;
  text: string;
}

function statePath(cwd: string): string {
  return path.join(cwd, STATE_DIR, STATE_FILE);
}

function readJoinState(cwd: string): JoinState | null {
  try {
    const parsed = JSON.parse(fs.readFileSync(statePath(cwd), "utf-8"));
    return typeof parsed?.name === "string" ? { name: parsed.name } : null;
  } catch {
    return null;
  }
}

function writeJoinState(cwd: string, name: string): void {
  fs.mkdirSync(path.join(cwd, STATE_DIR), { recursive: true });
  fs.writeFileSync(statePath(cwd), JSON.stringify({ name }, null, 2));
}

async function registerAs(pi: ExtensionAPI, ctx: ExtensionContext, name: string) {
  // --pid explicitly: without it, the CLI's own pid fallback (no adapter
  // reads *this* process's ancestry the way the MCP tool path does) resolves
  // to whatever process pi.exec spawned to run this command -- which is
  // dead the instant it returns. The entry writes, then the very next
  // prune_dead_roster() (any later `list`/`register` call) deletes it,
  // silently, before anyone observes it. process.pid is omp's own -- the
  // long-lived process this extension runs inside, with no isolation from
  // it (docs/extension-loading.md).
  return pi.exec(
    "agent-bus",
    ["register", "--name", name, "--kind", AGENT_BUS_KIND, "--pid", String(process.pid)],
    { cwd: ctx.cwd },
  );
}

export default function agentBusExtension(pi: ExtensionAPI) {
  pi.setLabel("agent-bus");

  // Silent re-registration on every future session_start once joined once --
  // "seen this session before, no need to re-register [by hand]." register()
  // is idempotent (a rename-in-place under the same pid, or a reconnect-by-
  // name-and-kind takeover under a new one), so calling it every session
  // start is cheap and safe; the point is the user never types the command
  // a second time for a project they have already joined.
  pi.on("session_start", async (_event, ctx) => {
    const joined = readJoinState(ctx.cwd);
    if (!joined) return; // never asked to participate -- stay passive
    const result = await registerAs(pi, ctx, joined.name);
    if (result.code !== 0) {
      ctx.ui.notify(`agent-bus: could not re-register as ${joined.name}: ${result.stderr}`, "warning");
    }
  });

  pi.registerCommand("agent-bus-join", {
    description: "Join the agent-bus peer bus under a name (once per project)",
    handler: async (args, ctx) => {
      const name = args.trim() || path.basename(ctx.cwd);
      const result = await registerAs(pi, ctx, name);
      if (result.code !== 0) {
        ctx.ui.notify(`agent-bus join failed: ${result.stderr}`, "error");
        return;
      }
      writeJoinState(ctx.cwd, name);
      ctx.ui.notify(`Joined agent-bus as ${name}. Future sessions in this project rejoin automatically.`, "info");
    },
  });

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

    // Explicit --target, not the calling subprocess's own implicit "self"
    // resolution: reads happen to get this right today (get_self() walks
    // ancestors and lands on omp's own long-lived pid, the direct parent of
    // whatever pi.exec spawns), the same way register() with no --pid gets
    // it *wrong* (its own fallback prefers an existing self entry, and has
    // none to prefer here, so it lands on the ephemeral subprocess's own
    // pid instead -- see registerAs() above, found live). Naming the target
    // explicitly removes the dependence on that ancestor-walk coincidence.
    const joined = readJoinState(ctx.cwd);
    const target = joined ? ["--target", joined.name] : [];

    const listed = await pi.exec("agent-bus", ["inbox", "--unread", "--json", ...target], { cwd: ctx.cwd });
    if (listed.code !== 0) {
      ctx.ui.notify(`agent-bus: could not read inbox: ${listed.stderr}`, "warning");
      return;
    }
    let messages: InboxMessage[];
    try {
      messages = JSON.parse(listed.stdout);
    } catch {
      return;
    }

    for (const msg of messages) {
      const full = await pi.exec("agent-bus", ["read", msg.id, "--json", ...target], { cwd: ctx.cwd });
      let text = msg.text;
      if (full.code === 0) {
        try {
          text = (JSON.parse(full.stdout) as InboxMessage).text ?? text;
        } catch {
          // fall back to the summary-shaped notice already in hand
        }
      }
      await pi.exec("agent-bus", ["ack", msg.id, ...target], { cwd: ctx.cwd });
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
