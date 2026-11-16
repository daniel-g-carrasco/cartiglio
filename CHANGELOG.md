# Changelog

What changes for whoever uses Cartiglio. The notes of the versions released
up to 1.1.6 are in `data/io.github.daniel_g_carrasco.cartiglio.metainfo.xml`
and on the GitHub releases.

## [1.1.7] - 2026-11-16

### Changed

- Windows: the check for a newer version, when it is on, is made when the
  window is first shown and a few seconds after it is restored from the
  taskbar, at most every 4 hours. Before, once a day at start only: a
  release published after the morning's check waited until the next day.
  The Preferences say "Check for updates automatically, at most every 4
  hours".
- The errors of the window are kept in `errors.log` too, in
  `%LOCALAPPDATA%\cartiglio\state` on Windows and `~/.local/state/cartiglio`
  on Linux (rotated past 1 MB to `errors.log.1`; a run without errors
  writes nothing). The command line still prints them in its terminal.
- The preferences (`settings.ini`, where they have always been) are written
  whole or not at all, and the save is tried again for a moment when
  Windows refuses it (an antivirus holding the file just written).
- Updates: a project with no release yet is not a failed check; the
  installer, once verified, is held open until Setup has read it; two
  messages name GitHub and the project ("unexpected answer from GitHub",
  "the installer is not signed by the project").

### Licence

- Cartiglio stays under the GNU AGPL, version 3 or later. It now carries
  modules shared with the author's other apps, under the GNU GPL, version 3
  or later (section 13 of both licences lets them be combined): the README
  and the About window say so.
- The text of the licence now comes with the program: in the installed
  folder, in the portable zip and in the Linux archive
  (`_internal/licenses/LICENSE`).
