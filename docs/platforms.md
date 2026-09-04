# 1. Supported platforms

aws-tui runs on macOS, Linux, and Windows through one shared application and
service architecture. Platform adapters handle native config and cache paths,
atomic file replacement and synchronization, local filesystem identity and
containment, and the operating-system credential store. Terminal and font
capabilities also differ by platform.

## 1.1. Quick reference

| OS | Recommended terminal | Recommended font | Config dir | Cache dir |
|---|---|---|---|---|
| **macOS** | Terminal.app, iTerm2, Warp, kitty | SF Mono (default), Fira Code, JetBrains Mono | `~/Library/Application Support/aws-tui` (new installs) or `~/.config/aws-tui` (existing) | `~/Library/Caches/aws-tui` or `~/.cache/aws-tui` |
| **Linux** | GNOME Terminal, Konsole, Alacritty, kitty, WezTerm | Any Nerd Font / Powerline-aware font | `$XDG_CONFIG_HOME/aws-tui` (defaults to `~/.config/aws-tui`) | `$XDG_CACHE_HOME/aws-tui` (defaults to `~/.cache/aws-tui`) |
| **Windows** | **Windows Terminal 1.18+** hosting **PowerShell** (5.1 or 7+) | Cascadia Code (Windows Terminal default) | `%APPDATA%\aws-tui` | `%LOCALAPPDATA%\aws-tui\Cache` |

The legacy `~/.config/aws-tui` location is preferred when it already
exists on disk (macOS / Linux upgrade path), so an existing install is
never silently abandoned for the new platform-native slot.

## 1.2. Windows — the supported launch path

The two things to get right on Windows are the **terminal host** and the
**font** — not the shell. PowerShell 5.1 (ships with Windows 10/11),
PowerShell 7+, and even `cmd.exe` all work; aws-tui doesn't shell out to
the launching command processor for anything user-visible.

### 1.2.1. Tested terminal host

aws-tui's banner, box-drawing borders, and 24-bit theme gradients all
need a terminal that supports:

- 24-bit truecolor escape sequences
- Unicode box-drawing characters (`╔ ═ ╗ ║ ╝`, `█ ▓ ▌`, etc.)
- The Kitty / VT mouse protocol so multi-select via Ctrl+Click works

**Windows Terminal 1.18 or newer** is the tested and recommended host. It
ships by default on Windows 11 and is a one-click install from the Microsoft
Store on Windows 10. Other modern truecolor terminals, including WezTerm, may
work but are not part of the supported test matrix. The
legacy `conhost.exe` (the console window you get if you launch
`powershell.exe` directly without Windows Terminal) does **not** support
24-bit color reliably and is **not supported**.

### 1.2.2. Launching

1. Open Windows Terminal.
2. Open a PowerShell tab (the default profile on Windows 11).
3. Install aws-tui from Git until the first PyPI release lands:
   ```powershell
   pipx install git+https://github.com/thekaveh/aws-tui.git
   # or, if you have uv installed system-wide:
   uv tool install git+https://github.com/thekaveh/aws-tui.git
   ```
   After aws-tui is visible on PyPI, the shorter
   `pipx install aws-tui` / `uv tool install aws-tui` commands become
   the preferred install path.
4. Run:
   ```powershell
   aws-tui
   ```

### 1.2.3. Font

Windows Terminal's default font is **Cascadia Code**, which already
ships with every box-drawing glyph aws-tui needs. If you've switched
to another font and the banner shows boxes instead of `█`, switch back
to Cascadia Code via *Settings → Profile → Appearance → Font face* —
or to any Nerd Font (e.g. Fira Code Nerd Font, JetBrains Mono Nerd
Font).

### 1.2.4. AWS profile resolution on Windows

The AWS CLI uses identical default paths on every OS: `~/.aws/config` and
`~/.aws/credentials`, where `~` is `%USERPROFILE%` on Windows
(`C:\Users\<you>\.aws\config`). `AWS_CONFIG_FILE` and
`AWS_SHARED_CREDENTIALS_FILE` override those paths; aws-tui expands `%VAR%`,
`$VAR`, and `~` in their values. Silent SSO discovery and the
`AWS_DEFAULT_PROFILE` / `AWS_PROFILE` startup precedence work the same way as
on macOS/Linux. See the README's "Environment variables" section.

## 1.3. macOS

Any modern terminal works: Terminal.app, iTerm2, Warp, kitty, Alacritty.
The default SF Mono font has the full box-drawing range; Fira Code or
JetBrains Mono are popular alternatives.

## 1.4. Linux

Any modern terminal emulator with truecolor support: GNOME Terminal,
Konsole, Alacritty, kitty, WezTerm. Install a Powerline-aware or Nerd
Font for full box-drawing glyph support.

If you set `$XDG_CONFIG_HOME` or `$XDG_CACHE_HOME`, aws-tui follows
them through `platformdirs` on fresh installs. One upgrade caveat:
when an existing legacy `~/.config/aws-tui` or `~/.cache/aws-tui`
directory is present, that legacy directory wins so existing users do
not silently launch against an empty config/cache root.

## 1.5. What's not supported

- **Legacy Windows `conhost.exe`** — no 24-bit color, missing glyphs.
- **PowerShell ISE** — ANSI sequences are printed literally instead of
  being interpreted.
- **`tmux` / `screen` over SSH without truecolor passthrough** — set
  `set-option -ga terminal-overrides ",xterm-256color:Tc"` in your
  `~/.tmux.conf` so the gradient survives the multiplexer.

## 1.6. Reporting a rendering bug

If the banner gradient or box-drawing chars look wrong on your stack,
please file an issue with:

- OS + version
- Terminal emulator + version
- Font + size
- Output of `echo $TERM` (Linux/macOS) or `$env:TERM` (PowerShell)
- A screenshot of the launch screen
