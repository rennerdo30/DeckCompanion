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
    const timer = setInterval(() => void refresh(), 10000);
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
  const current = game?.proton?.name;
  const options = [{ data: "auto", label: current && !game?.proton_override ? `Automatic (${current})` : "Automatic" },
    ...(snapshot?.proton ?? []).map(p => ({ data: p.name, label: p.name }))];
  if (game?.proton_override && !options.some(o => o.data === game.proton_override)) {
    options.push({ data: game.proton_override, label: `${game.proton_override} (missing)` });
  }
  function openApp() {
    if (!snapshot?.shortcut_appid) return;
    SteamClient.Apps.RunGame(snapshot.shortcut_appid, "", 0, 200); // Steam's _10ft launch source.
    Navigation.CloseSideMenus();
  }
  const playing = ["current", "running"].includes(snapshot?.selection ?? "");
  const summary = game ? [game.proton ? `${game.proton.name} · ${REASONS[game.proton.reason] ?? game.proton.reason}` : "No Proton installed",
    game.has_recipe ? "recipe" : ""].filter(Boolean).join(" · ") : "";

  // Decky's own text sizes: the Quick Access menu is narrow, so no size overrides here.
  return <div style={{ overflowWrap: "anywhere" }}>
    {error && <PanelSection><PanelSectionRow><div role="alert" style={{ color: "#ffb38a" }}>{error}</div></PanelSectionRow></PanelSection>}
    {game ? <PanelSection title={playing ? "Playing now" : "Last played"}>
      <PanelSectionRow><div>
        <div style={{ fontWeight: 600 }}>{game.name}</div>
        <div style={MUTED}>{summary}</div>
      </div></PanelSectionRow>
      <PanelSectionRow><ToggleField label="Use DeckCompanion" description="Fixes, mods and the right Proton for this game"
        checked={routing} disabled={busy || !routingChoice} onChange={value => void update("routing", value)} /></PanelSectionRow>
      {protonChoice && <PanelSectionRow><DropdownItem label="Proton" rgOptions={options}
        selectedOption={game.proton_override ?? "auto"} disabled={busy}
        onChange={value => void update("proton", value.data)} /></PanelSectionRow>}
      {game.mods.map(mod => <PanelSectionRow key={mod.id}>
        <ToggleField label={mod.name} description={MOD_STATES[mod.state] ?? mod.state_message}
          checked={mod.enabled} disabled={busy} onChange={enabled => void update("mod", { id: mod.id, enabled })} />
      </PanelSectionRow>)}
      {game.pending && <PanelSectionRow><div style={MUTED}>Takes effect after you apply it in DeckCompanion (Steam restarts once).</div></PanelSectionRow>}
      {game.pending && snapshot?.shortcut_appid && <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={openApp}>Apply in DeckCompanion</ButtonItem>
      </PanelSectionRow>}
    </PanelSection> : <PanelSection><PanelSectionRow>
      <div style={MUTED}>{snapshot ? "Play a game and its DeckCompanion setup shows up here." : "Loading…"}</div>
    </PanelSectionRow></PanelSection>}
    {snapshot && <PanelSection title="All games">
      <PanelSectionRow><ToggleField label="Launch splash" description="Shows Proton, fixes and mods when a game starts"
        checked={snapshot.settings.splash} disabled={busy} onChange={value => void update("splash", value)} /></PanelSectionRow>
      {!(game?.pending) && <PanelSectionRow><ButtonItem layout="below" disabled={!snapshot.shortcut_appid} onClick={openApp}>
        Open DeckCompanion</ButtonItem></PanelSectionRow>}
      {!snapshot.shortcut_appid && <PanelSectionRow><div style={MUTED}>Open DeckCompanion from your library once to add it here.</div></PanelSectionRow>}
    </PanelSection>}
  </div>;
}

const MUTED = { color: "#8b929a", fontSize: "0.9em" };
const REASONS: Record<string, string> = {
  pinned: "recipe pin", recipe_fallback: "recipe fallback", newest: "newest installed",
  valve_recommended: "Valve's pick", user_override: "your choice", community_fix: "community fix"
};
const MOD_STATES: Record<string, string> = {
  installed: "Installed", will_install_on_launch: "Installs on next launch",
  update_available: "Update available", off: "Off"
};

export default definePlugin(() => ({
  name: "DeckCompanion",
  titleView: <div className={staticClasses.Title}>DeckCompanion</div>,
  content: <Content />,
  // Sized like Decky's other icons (1em); without a size the SVG fills the whole tab.
  icon: <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" style={{ display: "block" }}>
    <path d="M7 6h10a5 5 0 0 1 5 5v2.5a3.5 3.5 0 0 1-6.2 2.2L14.6 14H9.4l-1.2 1.7A3.5 3.5 0 0 1 2 13.5V11a5 5 0 0 1 5-5z" />
    <path d="M9.5 10.5l1.8 1.8 3.2-3.3" />
  </svg>
}));
