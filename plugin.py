import sublime
import sublime_plugin
import os
import re
import json
import webbrowser

REQUIRE_REGEXP = '(require\s*\(?\s*[\'"])(.+?)[\'"]\s*\)?'
IMPORT_REGEXP = '((?:(?:import\s*(?:(?:.|\n)*?)(?:from)?)|(?:export\s*(?:(?:.|\n)+?)(?:from))\s*)[\'"])(.+?)[\'"]'

class EsFoldImportsListener(sublime_plugin.EventListener):

  def on_load_async(self, view):
    if not get_setting('auto_fold_imports', False):
      return

    filename = view.file_name()

    exts = get_setting('extensions')

    if filename.endswith(tuple(exts)):
      view.run_command('es_fold_imports')

class EsFoldImportsCommand(sublime_plugin.TextCommand):

  def run(self, edit):
    self.execute(IMPORT_REGEXP)

    self.switch()

  def execute(self, pattern):
    regions = self.view.find_all(pattern)
    if len(regions) == 0:
      return

    fn = self.view.unfold if self.is_folded() else self.view.fold

    fn(sublime.Region(regions[0].begin(), regions[-1].end()))

  def switch(self):
    self.folded = False if self.is_folded() else True

  def is_folded(self):
    return hasattr(self, 'folded') and self.folded is True

# |--------------------------------------------------------------------------
# | This command handles the clicks on the require and import statements.
# | It investigates if the current cursor position is on the top of one of this statements
# | and opens the selected file.
# |--------------------------------------------------------------------------
class OpenRequireUnderCursorCommand(sublime_plugin.TextCommand):

  def run(self, edit):
    view = self.view

    self._search_statements(view, REQUIRE_REGEXP) or \
      self._search_statements(view, IMPORT_REGEXP)

  def _search_statements(self, view, regexp):
    cursor_position = view.sel()[0]
    matches = view.find_all(regexp)

    for match in matches:
      if cursor_position.intersects(match):
        statement = view.substr(match)
        matcher = re.match(regexp, statement)
        module = matcher.group(len(matcher.groups()))
        open_module_file(view.window(), module)
        return True

# |--------------------------------------------------------------------------
# | This command is responsible for coloring the import and require statements
# | and handling the mouse hover on them.
# |--------------------------------------------------------------------------
class RequireEventListener(sublime_plugin.EventListener):

  def on_load_async(self, view):
    self._delete_cached_regions(view)
    self._underline_regions(view)

  def on_modified_async(self, view):
    self._delete_cached_regions(view)
    self._underline_regions(view)

  def on_hover(self, view, point, hover_zone):
    if not get_setting('show_popup_on_hover') \
      or not self._assert_in_right_file(view):
      return

    regions = self._find_regions(view)

    for region in regions:
      if region['region'].contains(point):
        return self._show_popup(view, region, point)

  def on_pre_close(self, view):
    self._delete_cached_regions(view)

  def _delete_cached_regions(self, view):
    if (hasattr(self, 'view_regions')) and (view.id() in self.view_regions):
      del self.view_regions[view.id()]

  ## Retrieves the regions from the current view containing import or require statements.
  def _find_regions(self, view):
    if not hasattr(self, 'view_regions'):
      self.view_regions = {}

    if view.id() in self.view_regions:
      return self.view_regions[view.id()]

    regions = []

    require_regions = view.find_all(REQUIRE_REGEXP)

    for region in require_regions:
      statement = view.substr(region)
      match = re.match(REQUIRE_REGEXP, statement)

      module = match.group(len(match.groups()))

      region.a += len(match.group(1))
      region.b = region.a + len(module)

      regions.append({ 'region': region, 'module': module, 'type': 'require' })

    import_regions = view.find_all(IMPORT_REGEXP)

    for region in import_regions:
      statement = view.substr(region)
      match = re.match(IMPORT_REGEXP, statement)

      module = match.group(len(match.groups()))

      region.a += len(match.group(1))
      region.b = region.a + len(module)

      regions.append({ 'region': region, 'module': module, 'type': 'import'  })

    self.view_regions[view.id()] = regions

    return regions

  def _underline_regions(self, view):
    if not self._assert_in_right_file(view):
      log('Skipping non js file')
      return

    regions = self._find_regions(view)
    regions = list(map(lambda x: x['region'], regions))
    scope = get_setting('scope')
    icon = get_setting('icon')
    underline = get_setting('underline')

    underline_bitmask = sublime.DRAW_NO_FILL|sublime.DRAW_NO_OUTLINE

    if underline:
      underline_bitmask |= sublime.DRAW_STIPPLED_UNDERLINE

    view.add_regions('requires', regions, scope, icon, underline_bitmask)

  def _show_popup(self, view, region, point):
    window = view.window()
    module = region['module']

    file = find_module(window, module)

    link = 'Module: <a href="%s">%s</a>' % (module, module)

    if module in CORE_MODULES:
      link += ' (opens browser)'
      description = '<p>Node.js core module</p>'
    elif not file:
      link = ''
      description = '<p>Module cannot be found!</p>'
      if not window.active_view().settings().get('webpack_resolve_modules'):
        description += '<br/><a href="help_%s">Setup import aliases</a>' % 'https://github.com/hajnalben/ClickableRequires#webpack-or-other-module-handlers'
    else:
      description = '<p>Found at: %s</p>' % file
      if not module.startswith('.'):
        description += '<br/><a href="npm_%s">View on npmjs.com</a>' % module.split('/')[0]

    html = link + description
    width = (len(description) - 5) * 10
    view.show_popup(html, sublime.HIDE_ON_MOUSE_MOVE_AWAY, point, width, on_navigate = lambda module: self._on_anchor_clicked(window, module))

  def _on_anchor_clicked(self, window, module):
    if module.startswith('npm_'):
      return webbrowser.open('https://www.npmjs.com/package/' + module[len('npm_'):], autoraise=True)
    elif module.startswith('help_'):
      return webbrowser.open(module[len('help_'):], autoraise=True)
    open_module_file(window, module)

  def _assert_in_right_file(self, view):
    window = view.window()
    if not window: return False

    ctx = window.extract_variables()
    if not 'file_name' in ctx: return False
    file_name = ctx['file_name']

    exts = get_setting('extensions')

    if not file_name.endswith(tuple(exts)):
      return False

    return True

# |--------------------------------------------------------------------------
# | Global functions
# |--------------------------------------------------------------------------

def open_module_file(window, module):
  file = find_module(window, module)

  if file:
    window.open_file(file)
    if get_setting('reveal_in_side_bar'):
      sublime.set_timeout(lambda: window.run_command('reveal_in_side_bar'), 100)
  else:
    webbrowser.open('https://nodejs.org/api/%s.html' % module, autoraise=True)

def find_module(window, module):
  ctx = window.extract_variables()
  file_path = ctx['file_path']

  match = find_require_module(module, file_path)

  if match:
    log('Found require module: ', match)

  if not match or not returnIfFile(match):
    project_path = ctx['project_path']
    webpack_modules = window.active_view().settings().get('webpack_resolve_modules')
    webpack_extensions = window.active_view().settings().get('webpack_resolve_extensions') or get_setting('resolve_extensions')

    match = find_import_module(module, project_path, webpack_modules, webpack_extensions)

    if match:
      log('Found import module: ', match)

  return returnIfFile(match)


def find_import_module(module, project_path, webpack_modules, webpack_extensions):
  if not webpack_modules:
      return

  for root in webpack_modules:
    for extension in webpack_extensions:
      folder_path = os.path.join(project_path, root)

      file = returnIfFile(os.path.join(folder_path, module)) \
        or returnIfFile(os.path.join(folder_path, module + extension)) \
        or returnIfFile(os.path.join(folder_path, module, 'index' + extension))

      if file:
        return file

# |--------------------------------------------------------------------------
# | The pseudocode of require: https://nodejs.org/api/modules.html#modules_all_together
# |--------------------------------------------------------------------------


"""
require(X) from module at path Y
  1. If X is a core module,
     a. return the core module
     b. STOP
  2. If X begins with '/'
     a. set Y to be the filesystem root
  3. If X begins with './' or '/' or '../'
     a. LOAD_AS_FILE(Y + X)
     b. LOAD_AS_DIRECTORY(Y + X)
  4. LOAD_NODE_MODULES(X, dirname(Y))
  5. THROW "not found"
"""
def find_require_module(module, file_path):
  if module in CORE_MODULES:
    return module

  if module.startswith('.'):
    path = os.path.normpath(os.path.join(file_path, module))
    return load_as_file(path) or load_as_directory(path)

  return load_node_modules(module, file_path)

"""
LOAD_AS_FILE(X)
  1. If X is a file, load X as JavaScript text.  STOP
  2. If X.js is a file, load X.js as JavaScript text.  STOP
  3. If X.json is a file, parse X.json to a JavaScript Object.  STOP
  4. If X.node is a file, load X.node as binary addon.  STOP
"""
def load_as_file(path):
  log('load_as_file: ', path)

  file = returnIfFile(path)

  if file: return file

  for extension in get_setting('resolve_extensions'):
    file = returnIfFile(path + extension)
    if file: return file

"""
LOAD_AS_DIRECTORY(X)
  1. If X/package.json is a file,
     a. Parse X/package.json, and look for "main" field.
     b. let M = X + (json main field)
     c. LOAD_AS_FILE(M)
     d. LOAD_INDEX(M)
  2. LOAD_INDEX(X)
"""
def load_as_directory(path):
  log('load_as_directory: ', path)
  package_path = returnIfFile(path, 'package.json')
  if package_path:
    with open(package_path, 'r', encoding='UTF-8') as package_json_contents:
      package_json = json.load(package_json_contents)
      main = package_json.get('main', 'index.js')
      main_path = os.path.join(path, main)
      return load_as_file(main_path) or load_index(main_path)
  else:
    return load_index(path)

"""
LOAD_INDEX(X)
  1. If X/index.js is a file, load X/index.js as JavaScript text.  STOP
  2. If X/index.json is a file, parse X/index.json to a JavaScript object. STOP
  3. If X/index.node is a file, load X/index.node as binary addon.  STOP
"""
def load_index(path):
  log('load_index: ', path)

  for extension in get_setting('resolve_extensions'):
    file = returnIfFile(path, 'index' + extension)
    if file: return file

"""
LOAD_NODE_MODULES(X, START)
  1. let DIRS=NODE_MODULES_PATHS(START)
  2. for each DIR in DIRS:
     a. LOAD_AS_FILE(DIR/X)
     b. LOAD_AS_DIRECTORY(DIR/X)
"""
def load_node_modules(module, start):
  log('load_node_modules: ', module, ' - ', start)
  dirs = node_modules_paths(start)
  for dir in dirs:
    path = os.path.join(dir, module)
    file = load_as_file(path) or load_as_directory(path)
    # Return only if the file is found!
    if file: return file

"""
NODE_MODULES_PATHS(START)
  1. let PARTS = path split(START)
  2. let I = count of PARTS - 1
  3. let DIRS = []
  4. while I >= 0,
     a. if PARTS[I] = "node_modules" CONTINUE (## this makes no sense :/)
     b. DIR = path join(PARTS[0 .. I] + "node_modules")
     c. DIRS = DIRS + DIR
     d. let I = I - 1
  5. return DIRS
"""
def node_modules_paths(start):
  log('node_modules_paths: ', start)
  parts = split_path(start)
  i = len(parts) - 1
  dirs = []
  while i >= 0:
    if parts[i] is 'node_modules':
      i = i - 1
      continue

    _parts = (parts[:i + 1] + ['node_modules'])
    dir = os.path.join(*_parts)
    dirs.append(dir)
    i = i - 1
  return dirs

def split_path(start):
  path = os.path.normpath(start)

  drive, path_and_file = os.path.splitdrive(path)
  path, file = os.path.split(path_and_file)

  folders = [file]

  while 1:
    path, folder = os.path.split(path)

    if folder != "":
      folders.append(folder)
    else:
      if path != "":
        folders.append(path)

      break

  folders.reverse()

  parts = folders

  if (drive):
    parts = [drive] + parts

  return parts

# Run in terminal: node -pe "require('repl')._builtinLibs"
CORE_MODULES = [
  'assert',
  'buffer',
  'child_process',
  'cluster',
  'crypto',
  'dgram',
  'dns',
  'domain',
  'events',
  'fs',
  'http',
  'https',
  'net',
  'os',
  'path',
  'punycode',
  'querystring',
  'readline',
  'repl',
  'stream',
  'string_decoder',
  'tls',
  'tty',
  'url',
  'util',
  'v8',
  'vm',
  'zlib'
]

# |--------------------------------------------------------------------------
# | Utility functions
# |--------------------------------------------------------------------------

SETTINGS_FILE = 'ClickableRequires.sublime-settings'

def get_setting(name, default = None):
  return sublime.load_settings(SETTINGS_FILE).get(name, default)

def log(*str):
  if get_setting('debug'): print(*str)

def returnIfFile(path, file = None):
  if not path:
    return

  _file = path

  if file:
    _file = os.path.join(path, file)

  if os.path.isfile(_file):
    return _file