# Shorekeeper for Claude

A customizable Windows companion for Claude Code, with animated reactions, paired voice clips and captions, adjustable bubbles, and portable profiles.

**v0.1.0 preview · Windows 10/11 x64**

This preview implements the connection for terminal Claude Code and Code sessions inside Claude Desktop. Live account integration has not yet been verified. Ordinary desktop chat and Cowork are planned for later.

## Start

Extract `Shorekeeper-Claude-Windows-v0.1.0-preview.zip` completely and run `守岸人Claude桌宠启动.exe`. Python is not required.

When ready for live integration, right-click the pet, open **外观、声音与迁移 → 连接 Claude**, enable the Claude Code connection, and start a new Code session. Use Claude Code 2.1.295 or later and sign in through Claude. Connection and automatic startup are disabled by default.

## Customize

- Assign GIF, WebP, PNG or JPEG artwork to each action.
- Change looping, playback speed and hold duration.
- Pick randomly from multiple voice clips, each with its own caption.
- Play the thinking reminder once per task; choose how often idle reminders play.
- Change fonts, font sizes and volume.
- Resize the pet, bubbles and quota display with live previews and draggable edges.
- Export all settings, artwork, fonts and audio, optionally including the Windows runtime.

Audio formats: WAV, MP3, OGG, FLAC, M4A and AAC. Font formats: TTF, OTF and TTC.

## Usage limits

Five-hour and weekly remaining usage are shown separately when supplied by the [official Claude Code status line](https://code.claude.com/docs/en/statusline). Other windows are displayed only when returned. Context usage and estimated session cost are separate from plan limits; extra usage balance is not available.

Missing data is shown as pending, expired windows wait for new data, and old values carry a cache marker. An existing custom status line is preserved; in that case only task events are connected. Desktop Code status-line reporting still needs live verification.

## Profiles

Use **保存与迁移** to save settings or export a ZIP containing your media and preferences. The full portable ZIP also includes the application. Credentials, conversations and usage caches are excluded. On a new computer, install and sign in to Claude separately.

The preview has not been published as a stable GitHub release. Update notification support is retained and disabled by default.

## Credits

Artwork source: [emoji.wuwa.games](https://emoji.wuwa.games/).

Community crowdfunding QQ group: **1079834905**.

Thank you to all Shorekeeper fans who contributed to the emote crowdfunding.

Character artwork and original voices belong to their respective rights holders. This is an unofficial fan companion, not an Anthropic or Kuro Games product. Component and font licenses are in `licenses/` and `THIRD_PARTY.txt`.

[中文说明](README.md) · [Detailed Chinese guide](docs/USAGE.md)
