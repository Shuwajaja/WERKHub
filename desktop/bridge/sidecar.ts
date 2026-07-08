// Deno module (run by Deno, not Vite). Spawns the werktools hub dashboard
// sidecar and waits until its loopback HTTP endpoint is healthy.

const PORT = 7879;
const HEALTH = `http://127.0.0.1:${PORT}/api/status`;

export interface Sidecar {
  proc: Deno.ChildProcess;
  token: string;
}

// Locate a bundled standalone backend (PyInstaller onefile) installed next to
// the app binary — this is what lets the packaged app run with no Python.
function bundledBackend(): string | null {
  const name =
    Deno.build.os === "windows" ? "werkhub-backend.exe" : "werkhub-backend";
  const sep = Deno.build.os === "windows" ? "\\" : "/";
  let dir: string;
  try {
    const p = Deno.execPath();
    dir = p.slice(0, p.lastIndexOf(sep));
  } catch {
    return null;
  }
  for (const candidate of [
    `${dir}${sep}${name}`,
    `${dir}${sep}backend${sep}${name}`,
  ]) {
    try {
      Deno.statSync(candidate);
      return candidate;
    } catch {
      // not present at this path
    }
  }
  return null;
}

// Resolution order: explicit override -> bundled standalone exe -> Python (dev).
// The `werktools` console script is not guaranteed on PATH, so the dev fallback
// invokes the CLI module through Python (the verified path on a dev host).
function backendCommand(): { exe: string; args: string[] } {
  const override = Deno.env.get("WERKHUB_BACKEND_CMD");
  if (override) {
    const parts = override.split(" ");
    return { exe: parts[0], args: [...parts.slice(1), "--port", String(PORT)] };
  }
  const bundled = bundledBackend();
  if (bundled) {
    return { exe: bundled, args: ["hub", "dashboard", "--port", String(PORT)] };
  }
  return {
    exe: "python",
    args: [
      "-c",
      "import sys; sys.argv=['werktools','hub','dashboard','--port','" +
        String(PORT) +
        "']; from werktools.cli import main; main()",
    ],
  };
}

export async function startSidecar(): Promise<Sidecar> {
  const { exe, args } = backendCommand();
  const cmd = new Deno.Command(exe, { args, stdout: "piped", stderr: "piped" });
  const proc = cmd.spawn();
  const token = await waitForHealth();
  return { proc, token };
}

async function waitForHealth(timeoutMs = 15000): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(HEALTH);
      if (res.ok) {
        // Token is only needed for POST/mutation calls. Read-only GETs over
        // loopback do not require it; default to "" and log which path was used.
        const token = res.headers.get("X-Werk-Token") ?? "";
        await res.body?.cancel();
        return token;
      }
    } catch {
      // sidecar not up yet
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error("sidecar did not become healthy within timeout");
}

if (import.meta.main) {
  const s = await startSidecar();
  console.log("healthy; token-present:", s.token.length > 0);
  s.proc.kill();
}
