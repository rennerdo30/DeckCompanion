"""Unprivileged Decky RPC adapter. All state belongs to the installed engine."""
import asyncio
import json
from pathlib import Path
import re
import decky

CLI_TIMEOUT = 20
MAX_OUTPUT = 2 * 1024 * 1024


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise ValueError("Expected a Steam appid")
    return value


class Plugin:
    async def _main(self):
        self.processes = set()

    async def _unload(self):
        for proc in list(self.processes):
            if proc.returncode is None:
                proc.kill()
                await asyncio.wait_for(proc.wait(), 2)

    async def _cli(self, *args):
        executable = Path(decky.DECKY_USER_HOME) / ".local/share/deckcompanion/bin/deckcompanion"
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(str(executable), *args, "--json",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            self.processes.add(proc)
            decky.logger.info("DeckCompanion CLI pid=%s", proc.pid)
            async def read():
                blocks, size = [], 0
                while block := await proc.stdout.read(65536):
                    size += len(block)
                    if size > MAX_OUTPUT:
                        raise ValueError("CLI response exceeds 2 MiB")
                    blocks.append(block)
                await proc.wait()
                return b"".join(blocks)
            output = await asyncio.wait_for(read(), CLI_TIMEOUT)
            result = json.loads(output)
            if not isinstance(result, dict):
                raise ValueError("CLI response is not an object")
            if proc.returncode and not result.get("error"):
                raise ValueError("CLI exited unsuccessfully")
            return result
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            return {"error": {"code": "cli_unavailable", "message": str(exc) or "CLI timed out after 20 seconds"}}
        finally:
            if proc:
                if proc.returncode is None:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), 2)
                self.processes.discard(proc)

    async def panel(self, appid=None):
        return await self._cli("decky", "panel", *(["--appid", identifier(appid)] if appid else []))

    async def change(self, appid, action, value):
        # No arbitrary command strings or shell. Frontend inputs have a finite vocabulary.
        ident = identifier(appid) if action != "splash" else None
        if action == "routing" and type(value) is bool:
            return await self._cli("enable" if value else "disable", ident)
        if action == "proton" and isinstance(value, str) and 0 < len(value) <= 128 and not value.startswith("-"):
            return await self._cli("game-option", ident, "--proton", value)
        if action == "splash" and type(value) is bool:
            return await self._cli("settings", "--splash", "on" if value else "off")
        if action == "mod" and isinstance(value, dict) and type(value.get("enabled")) is bool:
            mod = value.get("id")
            if isinstance(mod, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", mod):
                return await self._cli("mod", ident, mod, "--on" if value["enabled"] else "--off")
        raise ValueError("Unsupported DeckCompanion action")
