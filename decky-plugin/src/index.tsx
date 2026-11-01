import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, ToggleField,
  Router, Navigation, staticClasses } from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useEffect, useRef, useState } from "react";

type Game = {
  appid: string; name: string; kind?: string; enabled: boolean; pending?: string | null;
  has_recipe: boolean; recipe_version?: string; proton_override?: string | null;
  proton?: { name: string; reason: string }; last_launch?: { time: string; ok: boolean };
  options?: { proton?: boolean; takeover?: boolean };
  mods: { id: string; name: string; enabled: boolean; state: string; state_message: string }[];
};
type Snapshot = {
  game: Game | null; selection: string; settings: { splash: boolean };
  proton: { name: string }[]; shortcut_appid: string | null;
};
type Reply<T> = T & { error?: { message: string } };
const panel = callable<[appid: string | null], Reply<Snapshot>>("panel");
const change = callable<[appid: string | null, action: string, value: unknown], Reply<object>>("change");

function Content() {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const flight = useRef(false);
  const alive = useRef(true);
  const latest = useRef<Snapshot | undefined>(undefined);

  async function read() {
    const current = Router.MainRunningApp?.appid;
    const id = current && String(current) !== latest.current?.shortcut_appid ? String(current) : null;
    const reply = await panel(id);
    if (reply.error) throw new Error(reply.error.message);
    if (alive.current) { latest.current = reply; setSnapshot(reply); }
  }
  async function refresh() {
    if (flight.current) return;
    flight.current = true;
    if (alive.current) setBusy(true);
    try { await read(); if (alive.current) setError(""); }
    catch (e) { if (alive.current) setError(String(e)); }
    finally { flight.current = false; if (alive.current) setBusy(false); }
  }
  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => { alive.current = false; clearInterval(timer); };
  }, []);

  async function update(action: string, value: unknown) {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    try {
      const reply = await change(snapshot?.game?.appid ?? null, action, value);
      if (reply.error) throw new Error(reply.error.message);
      await read();
      if (alive.current) setError("");
    } catch (e) { if (alive.current) setError(String(e)); }
    finally { flight.current = false; if (alive.current) setBusy(false); }
  }
  const game = snapshot?.game;
  const protonChoice = game?.options?.proton ?? game?.kind !== "non_steam";
  const routingChoice = game?.options?.takeover ?? game?.kind !== "non_steam";
  const routing = game?.pending ? game.pending === "enable" : !!game?.enabled;
  const options = [{ data: "auto", label: "Automatic" },
    ...(snapshot?.proton ?? []).map(p => ({ data: p.name, label: p.name }))];
  if (game?.proton_override && !options.some(o => o.data === game.proton_override)) {
    options.push({ data: game.proton_override, label: `${game.proton_override} (missing)` });
  }

  return <div className="deckcompanion-qam" style={{ fontSize: 19, lineHeight: 1.4, overflowWrap: "anywhere" }}>
    <style>{`
      .deckcompanion-qam [class*="Label"], .deckcompanion-qam [class*="Description"],
      .deckcompanion-qam [class*="Button"] { font-size: 19px; white-space: normal; }
      .deckcompanion-qam button, .deckcompanion-qam [role="button"],
      .deckcompanion-qam [role="switch"] { min-height: 40px; }
      .deckcompanion-qam .gpfocus, .deckcompanion-qam :focus-visible {
        outline: 2px solid #8ce0c1; outline-offset: 2px;
      }
    `}</style>
    <PanelSection>
      {error && <PanelSectionRow><div role="alert">{error}</div></PanelSectionRow>}
      <PanelSectionRow><ButtonItem layout="below" disabled={busy || !snapshot?.shortcut_appid}
        onClick={() => {
          if (snapshot?.shortcut_appid) {
            SteamClient.Apps.RunGame(snapshot.shortcut_appid, "", 0, 200); // Steam's _10ft launch source.
            Navigation.CloseSideMenus();
          }
        }}>Open DeckCompanion</ButtonItem></PanelSectionRow>
      {snapshot && !snapshot.shortcut_appid && <PanelSectionRow>
        <div>Install the full app and Apply once to add its Steam shortcut.</div>
      </PanelSectionRow>}
      <PanelSectionRow><ButtonItem layout="below" disabled={busy} onClick={() => void refresh()}>
        Refresh
      </ButtonItem></PanelSectionRow>
    </PanelSection>
    {game ? <PanelSection>
      <PanelSectionRow><div>
        <div style={{ fontSize: 20, fontWeight: 600 }}>{game.name}</div>
        <div>{["current", "running"].includes(snapshot!.selection) ? "Running game" : "Last game"}</div>
        <div>{game.has_recipe ? `Recipe ${game.recipe_version ?? "available"}` : "No recipe; automatic Proton"}</div>
        <div>{game.proton ? `${game.proton.name} · ${game.proton.reason}` : "No installed Proton"}</div>
        <div>{game.last_launch ? `Last launch: ${new Date(game.last_launch.time).toLocaleString()} · ${game.last_launch.ok ? "handed off" : "failed"}` : "No DeckCompanion launch yet"}</div>
      </div></PanelSectionRow>
      <PanelSectionRow><ToggleField label="Run through DeckCompanion" checked={routing}
        disabled={busy || !routingChoice} onChange={value => void update("routing", value)} /></PanelSectionRow>
      {game.pending && <PanelSectionRow><div>Queued: {game.pending}. Open DeckCompanion and Apply when ready.</div></PanelSectionRow>}
      <PanelSectionRow><DropdownItem label="Proton" layout="below" rgOptions={options}
        selectedOption={game.proton_override ?? "auto"} disabled={busy || !protonChoice}
        onChange={value => void update("proton", value.data)} /></PanelSectionRow>
      {game.kind === "non_steam" && (!protonChoice || !routingChoice) && <PanelSectionRow>
        <div>Non-Steam games already run through DeckCompanion. Proton is chosen automatically (newest GE); per-game choice comes later.</div>
      </PanelSectionRow>}
      {game.mods.map(mod => <PanelSectionRow key={mod.id}>
        <ToggleField label={mod.name} description={`${mod.state}: ${mod.state_message}`}
          checked={mod.enabled} disabled={busy} onChange={enabled => void update("mod", { id: mod.id, enabled })} />
      </PanelSectionRow>)}
      {!game.mods.length && <PanelSectionRow><div>No mods in this recipe.</div></PanelSectionRow>}
    </PanelSection> : <PanelSection><PanelSectionRow>
      <div>{snapshot ? "Launch a game to see its setup here." : "Loading game setup…"}</div>
    </PanelSectionRow></PanelSection>}
    {snapshot && <PanelSection><PanelSectionRow>
      <ToggleField label="Launch splash" description="Applies to all games" checked={snapshot.settings.splash}
        disabled={busy} onChange={value => void update("splash", value)} />
    </PanelSectionRow></PanelSection>}
  </div>;
}

export default definePlugin(() => ({
  name: "DeckCompanion",
  titleView: <div className={staticClasses.Title} style={{ fontSize: 20 }}>DeckCompanion</div>,
  content: <Content />,
  // Sized like Decky's other icons (1em); without a size the SVG fills the whole tab.
  icon: <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" style={{ display: "block" }}>
    <path d="M7 6h10a5 5 0 0 1 5 5v2.5a3.5 3.5 0 0 1-6.2 2.2L14.6 14H9.4l-1.2 1.7A3.5 3.5 0 0 1 2 13.5V11a5 5 0 0 1 5-5z" />
    <path d="M9.5 10.5l1.8 1.8 3.2-3.3" />
  </svg>
}));
