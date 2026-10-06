"""The Cyclops look: one palette for the Zenity panels and the GTK folders window.

Zenity is themed through a private settings folder (GTK 3 and GTK 4 both read
gtk.css from $XDG_CONFIG_HOME), so the rest of the desktop is never touched.
If that folder cannot be made, dialogs simply use the desktop theme.
"""
import os
from pathlib import Path

from . import __version__

ICON = Path(__file__).resolve().parent.parent/'assets'/'cyclops-backup.svg'

BG, SURFACE, VIEW, LINE = '#0b1016', '#121a23', '#0f171f', '#1f2b38'
TEXT, DIM = '#d7e3ee', '#7f93a6'
EYE, EYE_INK = '#2fd3ff', '#04121a'
GOOD, WARN, BAD = '#46e39a', '#ffb547', '#ff6b6b'

GTK4_CSS = f"""
/* Cyclops Backup — private to its own dialogs */
@define-color accent_bg_color {EYE};
@define-color accent_fg_color {EYE_INK};
@define-color accent_color {EYE};
@define-color window_bg_color {BG};
@define-color window_fg_color {TEXT};
@define-color view_bg_color {VIEW};
@define-color view_fg_color {TEXT};
@define-color dialog_bg_color {BG};
@define-color dialog_fg_color {TEXT};
@define-color popover_bg_color {SURFACE};
@define-color popover_fg_color {TEXT};
@define-color headerbar_bg_color {BG};
@define-color headerbar_fg_color {TEXT};
@define-color card_bg_color {SURFACE};
@define-color card_fg_color {TEXT};
@define-color borders {LINE};
window, dialog, .background, .dialog-contents {{ background-color: {BG}; color: {TEXT}; }}
window.dialog {{ border: 1px solid {LINE}; }}
.title, .title-1, .title-2, .title-3 {{ color: {EYE}; }}
treeview, columnview, listview, textview, textview text, list, scrolledwindow, viewport {{
  background-color: {VIEW}; color: {TEXT}; }}
treeview header button, columnview > header > button {{ background: {SURFACE}; color: {DIM}; }}
columnview row:selected, listview row:selected, treeview:selected, list row:selected {{
  background-color: alpha({EYE}, 0.22); color: #ffffff; }}
entry, spinbutton {{ background-color: {SURFACE}; color: {TEXT}; border: 1px solid {LINE}; }}
checkbutton check:checked, radiobutton radio:checked, check:checked, radio:checked {{
  background-color: {EYE}; color: {EYE_INK}; }}
progressbar progress, progressbar > trough > progress {{ background-color: {EYE}; }}
progressbar trough, progressbar > trough {{ background-color: {LINE}; }}
button {{ border-radius: 8px; }}
button.suggested-action, .dialog-action-area button:last-child, .response-area button:last-child {{
  background: {EYE}; color: {EYE_INK}; font-weight: bold; }}
button.destructive-action {{ background: {BAD}; color: {EYE_INK}; }}
"""
GTK4_CSS += f"entry:focus-within {{ border-color: {EYE}; }}\n"

# GTK 3 (Zenity 3 on older systems, and the folders window).
GTK3_CSS = GTK4_CSS.split('entry:focus-within')[0] + f"""
entry:focus {{ border-color: {EYE}; }}
@define-color theme_bg_color {BG};
@define-color theme_fg_color {TEXT};
@define-color theme_base_color {VIEW};
@define-color theme_text_color {TEXT};
@define-color theme_selected_bg_color {EYE};
@define-color theme_selected_fg_color {EYE_INK};
.cyclops-header {{ background: {SURFACE}; border-bottom: 1px solid {LINE}; padding: 12px 16px; }}
.cyclops-brand {{ color: {EYE}; font-weight: 800; font-size: 15pt; letter-spacing: 2px; }}
.cyclops-sub {{ color: {DIM}; }}
.cyclops-footer {{ color: {DIM}; }}
treeview.view {{ background-color: {VIEW}; color: {TEXT}; }}
treeview.view:selected {{ background-color: alpha({EYE}, 0.25); color: #ffffff; }}
treeview.view header button {{ background: {SURFACE}; color: {DIM}; border-color: {LINE}; }}
combobox button, button {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {LINE}; box-shadow: none; }}
button:hover {{ border-color: {EYE}; }}
button:disabled {{ color: alpha({TEXT}, 0.35); }}
scrolledwindow.frame, frame > border, scrolledwindow undershoot, scrolledwindow overshoot {{ border-color: {LINE}; }}
scrolledwindow.frame {{ border: 1px solid {LINE}; }}
button.cyclops-primary {{ background: {EYE}; color: {EYE_INK}; font-weight: bold; border-color: {EYE}; }}
"""


def span(text, colour, bold=False, size=None):
    attrs = f' foreground="{colour}"' + (' weight="bold"' if bold else '') + (f' size="{size}"' if size else '')
    return f'<span{attrs}>{text}</span>'


def brand(subtitle=''):
    """Header line used at the top of every panel."""
    # The window title already reads "Cyclops Backup"; this is the eye mark and version.
    return span('◉', EYE, bold=True) + '  ' + span('v' + __version__ + (f'  ·  {subtitle}' if subtitle else ''), DIM, size='small')


def good(text):return span(text, GOOD, bold=True)
def warn(text):return span(text, WARN, bold=True)
def bad(text):return span(text, BAD, bold=True)
def dim(text):return span(text, DIM)


def _config_home():
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')


def theme_dir():
    """Private settings folder for Cyclops dialogs. Bookmarks and settings link to the real ones."""
    root = Path(os.environ.get('XDG_CACHE_HOME') or Path.home()/'.cache')/'cyclops-backup'/'look'
    real = _config_home()
    for version, css in (('gtk-4.0', GTK4_CSS), ('gtk-3.0', GTK3_CSS)):
        folder = root/version
        folder.mkdir(parents=True, exist_ok=True)
        target = folder/'gtk.css'
        if not target.exists() or target.read_text() != css:
            target.write_text(css)
        for name in ('settings.ini', 'bookmarks'):
            link, source = folder/name, real/version/name
            if source.exists() and not link.exists() and not link.is_symlink():
                link.symlink_to(source)
    return root


def dialog_env():
    """Environment for a Zenity call: the Cyclops stylesheet on top of the normal session."""
    env = dict(os.environ)
    if os.environ.get('CYCLOPS_BACKUP_PLAIN'):return env
    try:
        env['XDG_CONFIG_HOME'] = str(theme_dir())
    except OSError:
        pass
    return env


def apply_gtk3(Gtk, Gdk):
    """Style the in-process GTK 3 folders window."""
    settings = Gtk.Settings.get_default()
    if settings is not None:settings.set_property('gtk-application-prefer-dark-theme', True)
    provider = Gtk.CssProvider()
    provider.load_from_data(GTK3_CSS.encode())
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
