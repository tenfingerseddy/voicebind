These Qt 6 UI tests run the real popup components with a mock backend and a
small fixed theme. They never read or save user settings, keys or bookmarks.
They cover edits across pages, add/remove boundaries, picker index mapping,
bookmark phrase drafts, long-text loss/overflow, and controls fitting inside
600px and 520px panels.

Run from the project root (the unqualified qmltestrunner may be Qt 5):

```sh
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software QT_QUICK_CONTROLS_STYLE=Basic QT_FORCE_STDERR_LOGGING=1 /usr/lib/qt6/bin/qmltestrunner -input tests/qml -import tests/qml/imports
```
