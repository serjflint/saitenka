-- saitenka.lua — mpv user-script that launches the Saitenka overlay in ATTACH mode (Stage 16).
--
-- Installed by `saitenka-overlay install-plugin` into ~/.config/mpv/scripts/ (removable with
-- uninstall-plugin). On mpv start it ensures an IPC socket exists (creating a per-instance one via
-- the input-ipc-server client option if unset) and spawns `saitenka-overlay attach <socket>` as a
-- detached subprocess pointing at THIS mpv's own socket. mpv then works normally from ANY launcher;
-- we JOIN the socket rather than take it over (mpv allows many concurrent IPC clients).

-- Absolute path to the saitenka-overlay executable. `install-plugin` rewrites this line to the
-- resolved path, because a Finder/Dock-launched mpv does NOT inherit ~/.local/bin (or Homebrew's
-- bin) on PATH — a bare command name would fail to spawn. Left as the bare name when run unresolved.
local SAITENKA_BIN = 'saitenka-overlay'

local mp = require 'mp'
local utils = require 'mp.utils'

local function ensure_socket()
    local sock = mp.get_property('input-ipc-server')
    if sock == nil or sock == '' then
        -- create a per-instance socket/pipe so we have something to attach to
        if package.config:sub(1, 1) == '\\' then
            -- Windows: mpv IPC is a NAMED PIPE, not a filesystem socket (a /tmp/*.sock path is
            -- never connectable there). Use the \\.\pipe\ form the overlay's client expects.
            sock = '\\\\.\\pipe\\saitenka-mpv-' .. utils.getpid()
        else
            local tmp = os.getenv('TMPDIR') or '/tmp/'
            sock = tmp .. 'saitenka-mpv-' .. utils.getpid() .. '.sock'
        end
        mp.set_property('input-ipc-server', sock)
    end
    return sock
end

local spawned = false

local function spawn_overlay()
    if spawned then return end
    spawned = true
    local sock = ensure_socket()
    mp.command_native({
        name = 'subprocess',
        playback_only = false,
        detach = true,
        args = { SAITENKA_BIN, 'attach', sock },
    })
    mp.msg.info('saitenka: attached overlay on ' .. sock)
end

-- Spawn once the file is loaded (the socket and video dimensions are then available).
mp.register_event('file-loaded', spawn_overlay)
