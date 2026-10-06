"""View folders: one window for the active profile's folder list.

Profile dropdown on top, the folders (and anything left out of them) below,
and every folder action as a button. Changes save straight away; nothing is
copied from here. The window is GTK 3 (PyGObject, preinstalled on Zorin and
Ubuntu desktops); the control panel falls back to plain Zenity lists without it.
"""
from pathlib import Path
import re

from .config import BackupError, load_config, save_config
from . import __version__, look, setup

GTK_MISSING = 3   # exit code: window could not open, caller uses the Zenity fallback


def tilde(path):
    home = str(Path.home())
    return '~' + path[len(home):] if path == home or path.startswith(home + '/') else path


def suggested_name(source):
    return re.sub(r'[^A-Za-z0-9._-]+', '-', Path(source).name).strip('-._') or 'Project'


class FolderBook:
    """Everything the window does, without any widgets (so it can be tested)."""

    def __init__(self, config_path):
        self.path = Path(config_path)
        self.config = load_config(self.path, allow_empty=True)

    # read
    @property
    def profile(self):
        return self.config['profile']

    @property
    def profiles(self):
        return list(self.config['profiles'])

    @property
    def folders(self):
        return self.config['projects']

    def summary(self):
        n = len(self.folders)
        left = sum(len(p.get('excludes', [])) for p in self.folders)
        if not n:
            return 'No folders in this profile yet — use Add folder. Nothing is copied from this window.'
        text = f"{n} folder{'s' if n != 1 else ''}"
        if left:text += f" · {left} item{'s' if left != 1 else ''} left out"
        return text + ' — this is what Review and back up copies to the USB.'

    # write: every change is validated, saved, then kept
    def _apply(self, candidate):
        save_config(self.path, candidate)
        self.config = load_config(self.path, allow_empty=True)

    def switch(self, name):
        if name != self.profile:self._apply(setup.switch_profile(self.config, name))

    def new_profile(self, name, copy_current=False):
        self._apply(setup.new_profile(self.config, name, copy_current))

    def rename_profile(self, new):
        self._apply(setup.rename_profile(self.config, self.profile, new))

    def delete_profile(self):
        self._apply(setup.delete_profile(self.config, self.profile))

    def suggest(self, source):
        return setup.suggest_name(self.config, source)

    def add(self, source, name, archive=False):
        if not source:raise BackupError('Choose a folder first.')
        self._apply(setup.add_project(self.config, setup.clean_name(name), source, archive))

    def remove(self, name):
        self._apply(setup.remove_project(self.config, name))

    def leave_out(self, name, path, reason):
        self._apply(setup.add_exclusion(self.config, name, path, reason))

    def put_back(self, name, path):
        self._apply(setup.remove_exclusion(self.config, name, path))


def open_window(config_path):
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk, Pango
    except (ImportError, ValueError):
        return GTK_MISSING
    if not Gtk.init_check()[0]:
        return GTK_MISSING
    from gi.repository import GLib, Gdk
    GLib.set_prgname('cyclops-backup')
    GLib.set_application_name('Cyclops Backup')
    try:look.apply_gtk3(Gtk, Gdk)
    except Exception:pass   # a styling problem must never stop the folders window
    try:
        window = FoldersWindow(Gtk, Pango, FolderBook(config_path))
    except BackupError as exc:
        dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE, text=str(exc))
        dialog.set_title('Cyclops Backup');dialog.run();dialog.destroy()
        return 2
    window.connect('destroy', lambda *_: Gtk.main_quit())
    window.show_all()
    Gtk.main()
    return 0


def FoldersWindow(Gtk, Pango, book):
    """Built as a closure so importing this module never needs GTK."""
    from gi.repository import GLib
    win = Gtk.Window(title='Cyclops Backup — Folders')
    win.set_default_size(920, 580)
    win.set_position(Gtk.WindowPosition.CENTER)
    if look.ICON.exists():
        try:win.set_icon_from_file(str(look.ICON))
        except GLib.Error:win.set_icon_name('drive-removable-media')
    else:
        win.set_icon_name('drive-removable-media')

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    win.add(outer)
    header = Gtk.Box(spacing=12)
    header.get_style_context().add_class('cyclops-header')
    if look.ICON.exists():
        try:
            from gi.repository import GdkPixbuf
            header.pack_start(Gtk.Image.new_from_pixbuf(
                GdkPixbuf.Pixbuf.new_from_file_at_size(str(look.ICON), 40, 40)), False, False, 0)
        except (GLib.Error, ImportError, ValueError):
            pass
    titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    brand = Gtk.Label(label='CYCLOPS BACKUP', xalign=0)
    brand.get_style_context().add_class('cyclops-brand')
    sub = Gtk.Label(label=f'Folders  ·  v{__version__}', xalign=0)
    sub.get_style_context().add_class('cyclops-sub')
    titles.pack_start(brand, False, False, 0)
    titles.pack_start(sub, False, False, 0)
    header.pack_start(titles, False, False, 0)
    outer.pack_start(header, False, False, 0)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    for side in ('top', 'bottom', 'start', 'end'):getattr(box, 'set_margin_'+side)(14)
    outer.pack_start(box, True, True, 0)

    # profile row
    top = Gtk.Box(spacing=8)
    top.pack_start(Gtk.Label(label='Profile:'), False, False, 0)
    combo = Gtk.ComboBoxText()
    combo.set_size_request(220, -1)
    top.pack_start(combo, False, False, 0)
    buttons = {}
    for key, label in (('new', 'New…'), ('rename', 'Rename…'), ('delete', 'Delete')):
        buttons[key] = Gtk.Button(label=label)
        top.pack_start(buttons[key], False, False, 0)
    box.pack_start(top, False, False, 0)

    summary = Gtk.Label(xalign=0)
    summary.set_line_wrap(True)
    box.pack_start(summary, False, False, 0)

    # folder list: folders on top level, left-out items as children
    store = Gtk.TreeStore(str, str, str, str, str, str)   # name, source, on usb, note, folder, excluded path
    tree = Gtk.TreeView(model=store)
    for i, title in enumerate(('Folder', 'On this computer', 'On the USB', 'Note')):
        cell = Gtk.CellRendererText()
        if i in (1, 3):   # long columns shrink; names and USB locations stay readable
            cell.set_property('ellipsize', Pango.EllipsizeMode.MIDDLE if i == 1 else Pango.EllipsizeMode.END)
        column = Gtk.TreeViewColumn(title, cell, text=i)
        column.set_resizable(True)
        column.set_expand(i in (1, 3))
        column.set_min_width((0, 220, 0, 140)[i])
        tree.append_column(column)
    scroller = Gtk.ScrolledWindow()
    scroller.set_vexpand(True)
    scroller.set_shadow_type(Gtk.ShadowType.IN)
    scroller.add(tree)
    box.pack_start(scroller, True, True, 0)

    bottom = Gtk.Box(spacing=8)
    for key, label in (('add', 'Add folder…'), ('remove', 'Remove folder'), ('file', 'Leave out a file…'),
                       ('subfolder', 'Leave out a subfolder…'), ('back', 'Put back')):
        buttons[key] = Gtk.Button(label=label)
        bottom.pack_start(buttons[key], False, False, 0)
    buttons['add'].get_style_context().add_class('cyclops-primary')
    close = Gtk.Button(label='Close')
    close.connect('clicked', lambda *_: win.destroy())
    bottom.pack_end(close, False, False, 0)
    box.pack_start(bottom, False, False, 0)

    hint = Gtk.Label(xalign=0)
    hint.get_style_context().add_class('cyclops-footer')
    hint.set_markup('<small>Changes save straight away. Removing or leaving out never touches the folder '
                    'on this computer or the copy already on the USB. Nothing is copied from this window.</small>')
    hint.set_line_wrap(True)
    box.pack_start(hint, False, False, 0)

    state = {'filling': False}

    def selected():
        """(folder name, excluded path or None) of the highlighted row."""
        model, it = tree.get_selection().get_selected()
        if it is None:return None, None
        return model[it][4], (model[it][5] or None)

    def refresh():
        state['filling'] = True
        combo.remove_all()
        for i, name in enumerate(book.profiles):
            combo.append(name, name)
            if name == book.profile:combo.set_active(i)
        state['filling'] = False
        store.clear()
        for p in book.folders:
            kind = 'Evidence/archive' if p['destination'].startswith('Archives/') else 'Project'
            left = len(p.get('excludes', []))
            note = kind + (f" · {left} left out" if left else '')
            parent = store.append(None, [p['name'], tilde(p['source']), p['destination'], note, p['name'], ''])
            for e in p.get('excludes', []):
                store.append(parent, ['left out', e['path'], 'not copied', e['reason'], p['name'], e['path']])
        tree.expand_all()
        summary.set_text(book.summary())
        buttons['delete'].set_sensitive(len(book.profiles) > 1)
        update_buttons()

    def update_buttons(*_):
        folder, excluded = selected()
        for key in ('remove', 'file', 'subfolder'):buttons[key].set_sensitive(folder is not None)
        buttons['back'].set_sensitive(excluded is not None)

    def error(exc):
        message(Gtk.MessageType.ERROR, str(exc))

    def message(kind, text, secondary=None):
        d = Gtk.MessageDialog(transient_for=win, modal=True, message_type=kind,
                              buttons=Gtk.ButtonsType.CLOSE, text=text)
        if secondary:d.format_secondary_text(secondary)
        d.run();d.destroy()
        back_to_front()

    def confirm(text, secondary, ok_label):
        d = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.NONE, text=text)
        d.format_secondary_text(secondary)
        d.add_buttons('Cancel', Gtk.ResponseType.CANCEL, ok_label, Gtk.ResponseType.OK)
        ok = d.run() == Gtk.ResponseType.OK
        d.destroy()
        back_to_front()
        return ok

    def form(title, rows, ok_label='OK', read=lambda: True):
        """Small dialog: rows are (label, widget). On OK returns read(), called while the
        dialog's widgets still exist (they are emptied when it closes); None on Cancel."""
        d = Gtk.Dialog(title=title, transient_for=win, modal=True)
        d.add_buttons('Cancel', Gtk.ResponseType.CANCEL, ok_label, Gtk.ResponseType.OK)
        d.set_default_response(Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=10, row_spacing=10)
        for side in ('top', 'bottom', 'start', 'end'):getattr(grid, 'set_margin_'+side)(12)
        for r, (label, widget) in enumerate(rows):
            if label:grid.attach(Gtk.Label(label=label, xalign=0), 0, r, 1, 1)
            grid.attach(widget, 1 if label else 0, r, 1 if label else 2, 1)
            if isinstance(widget, Gtk.Entry):widget.set_activates_default(True)
        d.get_content_area().add(grid)
        d.show_all()
        values = read() if d.run() == Gtk.ResponseType.OK else None
        d.destroy()
        back_to_front()
        return values

    def back_to_front():
        # Keep this window up after a dialog closes instead of dropping behind others.
        win.deiconify();win.present()

    def act(fn):
        try:fn()
        except (BackupError, OSError, ValueError) as exc:error(exc)
        refresh()
        back_to_front()

    # profile actions
    def on_combo(*_):
        if not state['filling'] and combo.get_active_id():
            act(lambda: book.switch(combo.get_active_id()))

    def on_new(*_):
        entry = Gtk.Entry(); entry.set_width_chars(28)
        copy = Gtk.CheckButton(label=f'Start with a copy of "{book.profile}" folders')
        got = form('New profile', [('Name', entry), (None, copy)], 'Create',
                   lambda: (entry.get_text().strip(), copy.get_active()))
        if got:act(lambda: book.new_profile(*got))

    def on_rename(*_):
        entry = Gtk.Entry(text=book.profile); entry.set_width_chars(28)
        got = form('Rename profile', [('New name', entry)], 'Rename', lambda: entry.get_text().strip())
        if got is not None:act(lambda: book.rename_profile(got))

    def on_delete(*_):
        if confirm(f'Delete profile "{book.profile}"?',
                   'Only this list of folders is forgotten. The folders on this computer and '
                   'any copies already on the USB are not touched.', 'Delete profile'):
            act(book.delete_profile)

    # folder actions
    def on_add(*_):
        chooser = Gtk.FileChooserButton(title='Choose a folder', action=Gtk.FileChooserAction.SELECT_FOLDER)
        chooser.set_width_chars(34)
        name = Gtk.Entry(); name.set_width_chars(28)
        project = Gtk.RadioButton.new_with_label(None, 'Project — stored under Projects on the USB')
        evidence = Gtk.RadioButton.new_with_label_from_widget(project, 'Evidence/archive — stored under Archives')
        shows = Gtk.Label(xalign=0)

        def update(*_):
            clean = setup.clean_name(name.get_text())
            where = ('Archives/' if evidence.get_active() else 'Projects/') + (clean or '…')
            shows.set_markup(f'<small>Saved on the USB as <b>{GLib.markup_escape_text(where)}</b>'
                             '  ·  letters, numbers, . _ - (spaces become -)</small>')

        def picked(c):
            if c.get_filename():name.set_text(book.suggest(c.get_filename()))
        chooser.connect('file-set', picked)
        name.connect('changed', update)
        evidence.connect('toggled', update)
        update()
        got = form(f'Add a folder to "{book.profile}"',
                   [('Folder', chooser), ('Backup name', name), (None, shows), ('Kind', project), (None, evidence)],
                   'Add', lambda: (chooser.get_filename(), name.get_text(), evidence.get_active()))
        if got:act(lambda: book.add(*got))

    def on_remove(*_):
        folder, _ = selected()
        if folder and confirm(f'Stop backing up {folder} in "{book.profile}"?',
                              'The folder on this computer is not touched, and its existing copy on '
                              'the USB is kept.', 'Remove from profile'):
            act(lambda: book.remove(folder))

    def leave_out(directory):
        folder, _ = selected()
        if not folder:return
        source = next(p['source'] for p in book.folders if p['name'] == folder)
        action = Gtk.FileChooserAction.SELECT_FOLDER if directory else Gtk.FileChooserAction.OPEN
        d = Gtk.FileChooserDialog(title=f'Choose what to leave out of {folder}', transient_for=win, action=action)
        d.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'Choose', Gtk.ResponseType.OK)
        d.set_current_folder(source)
        picked = d.get_filename() if d.run() == Gtk.ResponseType.OK else None
        d.destroy()
        back_to_front()
        if not picked:return
        reason = Gtk.Entry(); reason.set_width_chars(40)
        reason.set_placeholder_text('e.g. cache, downloads, build output')
        why = form('Why is it safe to leave this out?',
                   [(None, Gtk.Label(label='Only skip things you can rebuild.', xalign=0)), ('Reason', reason)],
                   'Leave out', lambda: reason.get_text())
        if why is not None:act(lambda: book.leave_out(folder, picked, why))

    def on_back(*_):
        folder, excluded = selected()
        if excluded:act(lambda: book.put_back(folder, excluded))

    combo.connect('changed', on_combo)
    buttons['new'].connect('clicked', on_new)
    buttons['rename'].connect('clicked', on_rename)
    buttons['delete'].connect('clicked', on_delete)
    buttons['add'].connect('clicked', on_add)
    buttons['remove'].connect('clicked', on_remove)
    buttons['file'].connect('clicked', lambda *_: leave_out(False))
    buttons['subfolder'].connect('clicked', lambda *_: leave_out(True))
    buttons['back'].connect('clicked', on_back)
    tree.get_selection().connect('changed', update_buttons)
    refresh()
    win.book, win.refresh, win.buttons, win.combo, win.store = book, refresh, buttons, combo, store
    win.on_add, win.on_new, win.on_rename = on_add, on_new, on_rename
    return win
