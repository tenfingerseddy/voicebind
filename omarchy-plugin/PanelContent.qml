import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import qs.Commons

FocusScope {
    id: root
    required property var backend
    property string page: "Voice"
    onPageChanged: if (page === "History") backend.refreshHistory()
    property var draft: null
    property bool dirty: false
    property string deleteName: ""
    property string voicePage: "Listening"
    property var bookmarkEdits: ({})
    function modelSlice(model, first, end) {
        var rows = []
        for (var i = first; i < end; i++) rows.push({row: model.get(i), sourceIndex: i})
        return rows
    }
    signal closeRequested()
    ListModel { id: phrases }
    ListModel { id: aliases }
    function load() {
        if (!backend.document) return
        draft = JSON.parse(JSON.stringify(backend.document.config))
        phrases.clear(); aliases.clear()
        Object.keys(draft.phrases || {}).forEach(function(k) { phrases.append({spoken: k, command: draft.phrases[k]}) })
        var apps = Object.assign({}, backend.document.roles, draft.apps || {})
        Object.keys(apps).forEach(function(k) { aliases.append({spoken: k, app: apps[k]}) })
        wake.text = draft.voice.wake_phrase
        silence.text = String(draft.recognition.end_silence_ms)
        size.text = String(draft.indicator.size); top.text = String(draft.indicator.top)
        pill.text = String(draft.indicator.dictation_width)
        var sources = backend.document.microphones || []
        microphone.currentIndex = Math.max(0, sources.findIndex(function(s) { return s.name === (draft.recognition.source || "") }))
        dirty = false
    }
    function save() {
        if (!draft) return
        var cfg = JSON.parse(JSON.stringify(draft)), seen = {}
        cfg.voice.wake_phrase = wake.text.trim().toLowerCase()
        cfg.recognition.end_silence_ms = Number(silence.text)
        var source = (backend.document.microphones || [])[microphone.currentIndex]
        cfg.recognition.source = source ? source.name : ""
        cfg.indicator.size = Number(size.text); cfg.indicator.top = Number(top.text)
        cfg.indicator.dictation_width = Number(pill.text)
        cfg.phrases = {}; cfg.apps = {}
        for (var i = 0; i < phrases.count; i++) {
            var p = phrases.get(i), name = p.spoken.trim()
            if (!name && !p.command.trim()) continue
            var key = name.toLowerCase()
            if (seen[key]) { backend.notice = "Each phrase must have a unique name."; backend.failed = true; return }
            seen[key] = true; cfg.phrases[name] = p.command.trim()
        }
        for (var j = 0; j < aliases.count; j++) {
            var a = aliases.get(j), alias = a.spoken.trim().toLowerCase()
            if (!alias) continue
            if (cfg.apps[alias]) { backend.notice = "Each app name must be unique."; backend.failed = true; return }
            cfg.apps[alias] = a.app
        }
        backend.request({action: "save", config: cfg, revision: backend.document.revision, api_key: apiKey.text.trim()})
        apiKey.text = ""
    }
    function book(action, name, revision, extra) {
        var value = Object.assign({action: "bookmark." + action, name: name, revision: revision}, extra || {})
        backend.request(value)
        if (action === "restore") closeRequested()
    }
    Component.onCompleted: load()
    Connections { target: root.backend; function onLoaded() { root.load() } }
    Keys.onEscapePressed: closeRequested()
    ColumnLayout {
        anchors.fill: parent
        spacing: Style.space(10)
        RowLayout {
            Icon { Layout.preferredWidth: Style.space(27); Layout.preferredHeight: Style.space(27); foreground: Color.popups.text }
            Label { Layout.fillWidth: true; text: "Voicebind"; font.pixelSize: Style.font.heading; font.bold: true }
            Button { text: backend.online ? (backend.status.wake_enabled ? "Pause wake" : "Resume wake") : "Start"; enabled: !backend.busy && root.draft !== null; onClicked: backend.online ? backend.control("toggle-wake") : backend.request({action: "start"}) }
            Button { text: "Cancel"; visible: backend.online && backend.status.phase !== "idle"; onClicked: backend.control("cancel") }
        }
        Label {
            Layout.fillWidth: true
            text: !backend.online ? "Listener stopped" : backend.status.phase === "idle" ? (backend.status.wake_enabled ? "Ready · say “" + (root.draft ? root.draft.voice.wake_phrase : "computer") + "” or hold F10" : "Wake paused · F10 still works") : backend.status.phase
            color: Color.muted
        }
        Label { Layout.fillWidth: true; visible: backend.status.phase === "waiting" && !!backend.status.prompt; text: backend.status.prompt || ""; color: Color.accent }
        RowLayout {
            visible: root.draft !== null
            spacing: Style.space(6)
            Repeater {
                model: ["Voice", "Phrases", "Apps", "Bookmarks", "History"]
                Button { required property string modelData; text: modelData; primary: root.page === modelData; onClicked: root.page = modelData }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Color.popups.border }
        Label { Layout.fillWidth: true; visible: backend.busy || backend.notice !== ""; text: backend.busy ? "Working…" : backend.notice; color: backend.failed ? Color.urgent : Color.accent; maximumLineCount: 2; elide: Text.ElideRight }
        Item {
            id: body
            Layout.fillWidth: true; Layout.fillHeight: true
            ColumnLayout {
                anchors { left: parent.left; right: parent.right; top: parent.top }
                visible: root.draft === null && !backend.busy
                spacing: Style.space(16)
                Label { text: "Set up Voicebind"; font.bold: true; font.pixelSize: Style.font.heading }
                Label { Layout.fillWidth: true; text: "The bar extension is installed. Follow the setup guide to add local speech recognition and enable your microphone and F10 shortcuts." }
                Label { Layout.fillWidth: true; text: "Setup downloads the English speech model (148 MB). A Jev API key is optional."; color: Color.muted }
                RowLayout {
                    Button { text: "Setup guide"; primary: true; onClicked: Qt.openUrlExternally("https://github.com/tenfingerseddy/voicebind#install") }
                    Button { text: "Retry"; onClicked: backend.request({action: "panel"}) }
                }
            }
            ColumnLayout {
                anchors.fill: parent
                visible: root.page === "Voice" && root.draft !== null
                spacing: Style.space(10)
                RowLayout {
                    Repeater {
                        model: ["Listening", "Appearance", "Jev"]
                        Button { required property string modelData; text: modelData; primary: root.voicePage === modelData; onClicked: root.voicePage = modelData }
                    }
                }
                ColumnLayout {
                    visible: root.voicePage === "Listening"
                    Layout.fillWidth: true
                    spacing: Style.space(10)
                    GridLayout {
                        Layout.fillWidth: true; columns: 2; columnSpacing: Style.space(14); rowSpacing: Style.space(8)
                        Label { text: "Wake phrase" }
                        Field { id: wake; objectName: "wake"; Layout.fillWidth: true; onTextEdited: root.dirty = true }
                        Label { text: "Microphone" }
                        Select { id: microphone; objectName: "microphone"; Layout.fillWidth: true; model: backend.document ? backend.document.microphones : []; textRole: "description"; onActivated: root.dirty = true }
                        Label { text: "Silence wait (ms)" }
                        Field { id: silence; objectName: "silence"; Layout.fillWidth: true; validator: IntValidator { bottom: 200; top: 1500 } onTextEdited: root.dirty = true }
                    }
                    Label { Layout.fillWidth: true; text: "200–1500 ms of quiet ends a wake command. Lower is faster; raise it if pauses cut commands short. F10 release skips this wait."; color: Color.muted }
                    RowLayout {
                        Button { text: root.draft && root.draft.voice.wake_enabled ? "Wake at startup: on" : "Wake at startup: off"; onClicked: { root.draft.voice.wake_enabled = !root.draft.voice.wake_enabled; root.draft = Object.assign({}, root.draft); root.dirty = true } }
                        Button { text: root.draft && root.draft.desktop.focus_by_default ? "Follow apps: on" : "Follow apps: off"; onClicked: { root.draft.desktop.focus_by_default = !root.draft.desktop.focus_by_default; root.draft = Object.assign({}, root.draft); root.dirty = true } }
                    }
                    Label { Layout.fillWidth: true; text: "Say “computer dictate” to write, then “computer finish” to stop."; color: Color.muted }
                    Button { text: "Stop listener"; visible: backend.online; enabled: !backend.busy && !root.dirty; onClicked: backend.request({action: "stop"}) }
                }
                ColumnLayout {
                    visible: root.voicePage === "Appearance"
                    Layout.fillWidth: true; spacing: Style.space(12)
                    Label { Layout.fillWidth: true; text: "The circle and dictation waveform follow your Omarchy theme."; color: Color.muted }
                    Button { text: root.draft && root.draft.indicator.enabled ? "Listening indicator: on" : "Listening indicator: off"; onClicked: { root.draft.indicator.enabled = !root.draft.indicator.enabled; root.draft = Object.assign({}, root.draft); root.dirty = true } }
                    GridLayout {
                        Layout.fillWidth: true; columns: 2; columnSpacing: Style.space(14); rowSpacing: Style.space(10)
                        Label { text: "Circle size (px)" }
                        Field { id: size; objectName: "size"; Layout.fillWidth: true; validator: IntValidator { bottom: 28; top: 96 } onTextEdited: root.dirty = true }
                        Label { text: "Distance from top (px)" }
                        Field { id: top; objectName: "top"; Layout.fillWidth: true; validator: IntValidator { bottom: 0; top: 240 } onTextEdited: root.dirty = true }
                        Label { text: "Dictation pill width (px)" }
                        Field { id: pill; objectName: "pill"; Layout.fillWidth: true; validator: IntValidator { bottom: 120; top: 480 } onTextEdited: root.dirty = true }
                    }
                }
                ColumnLayout {
                    visible: root.voicePage === "Jev"
                    Layout.fillWidth: true; spacing: Style.space(12)
                    Label { text: "Jev API key · " + (backend.document && backend.document.key_configured ? "configured" : "not configured"); font.bold: true }
                    Label { Layout.fillWidth: true; text: "Jev interprets commands that need more flexible wording. Speech recognition runs locally."; color: Color.muted }
                    Field { id: apiKey; objectName: "apiKey"; Layout.fillWidth: true; echoMode: TextInput.Password; placeholderText: "Paste a key to replace it"; onTextEdited: root.dirty = true }
                    Label { Layout.fillWidth: true; text: "Stored privately on this computer. Leave blank to keep your current key, then use Save and apply."; color: Color.muted }
                }
                Item { Layout.fillHeight: true }
            }
            ColumnLayout {
                anchors.fill: parent
                visible: root.page === "Phrases"
                spacing: Style.space(10)
                Label { Layout.fillWidth: true; text: "Say it your way. Map a phrase to a command or chain. Leave out “computer”."; color: Color.muted }
                Item {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    ColumnLayout {
                        anchors { left: parent.left; right: parent.right; top: parent.top }
                        spacing: Style.space(14)
                        Repeater {
                            model: root.modelSlice(phrases, phrasePager.first, phrasePager.end)
                            ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true; spacing: Style.space(6)
                                RowLayout {
                                    Field { Layout.fillWidth: true; objectName: "phrase-" + modelData.sourceIndex; text: modelData.row.spoken; placeholderText: "What you say, e.g. get to work"; onTextEdited: { phrases.setProperty(modelData.sourceIndex, "spoken", text); root.dirty = true } }
                                    Button { text: "−"; Accessible.name: "Remove phrase"; onClicked: { root.dirty = true; phrases.remove(modelData.sourceIndex) } }
                                }
                                Field { Layout.fillWidth: true; objectName: "command-" + modelData.sourceIndex; text: modelData.row.command; placeholderText: "open teams on workspace 5 then make it full screen"; onTextEdited: { phrases.setProperty(modelData.sourceIndex, "command", text); root.dirty = true } }
                            }
                        }
                        Label { Layout.fillWidth: true; visible: phrases.count === 0; text: "Add your first phrase, e.g. “write this” → “dictate”."; color: Color.muted }
                    }
                }
                RowLayout {
                    Pager { id: phrasePager; objectName: "phrasePager"; Layout.fillWidth: true; count: phrases.count; pageSize: Math.max(1, Math.min(3, Math.floor((body.height - Style.space(108)) / Style.space(96)))) }
                    Button { text: "+ Add phrase"; onClicked: { phrases.append({spoken: "", command: ""}); phrasePager.showLast(); root.dirty = true } }
                }
            }
            ColumnLayout {
                anchors.fill: parent
                visible: root.page === "Apps"
                spacing: Style.space(10)
                Label { Layout.fillWidth: true; text: "Choose what everyday names like browser, files and email open."; color: Color.muted }
                Item {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    ColumnLayout {
                        anchors { left: parent.left; right: parent.right; top: parent.top }
                        spacing: Style.space(10)
                        Repeater {
                            model: root.modelSlice(aliases, appPager.first, appPager.end)
                            RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                Field { Layout.preferredWidth: Style.space(140); objectName: "alias-" + modelData.sourceIndex; text: modelData.row.spoken; placeholderText: "Spoken name"; onTextEdited: { aliases.setProperty(modelData.sourceIndex, "spoken", text); root.dirty = true } }
                                Select {
                                    Layout.fillWidth: true
                                    model: backend.document ? backend.document.apps : []
                                    textRole: "name"
                                    currentIndex: model.findIndex(function(a) { return a.id === modelData.row.app })
                                    onActivated: { aliases.setProperty(modelData.sourceIndex, "app", model[currentIndex].id); root.dirty = true }
                                }
                                Button { text: "−"; Accessible.name: "Remove app name"; onClicked: { root.dirty = true; aliases.remove(modelData.sourceIndex) } }
                            }
                        }
                    }
                }
                RowLayout {
                    Pager { id: appPager; objectName: "appPager"; Layout.fillWidth: true; count: aliases.count; pageSize: Math.max(1, Math.floor((body.height - Style.space(96)) / Style.space(48))) }
                    Button { text: "+ Add name"; enabled: backend.document && backend.document.apps.length > 0; onClicked: { aliases.append({spoken: "", app: backend.document.apps[0].id}); appPager.showLast(); root.dirty = true } }
                }
            }
            ColumnLayout {
                anchors.fill: parent
                visible: root.page === "Bookmarks"
                spacing: Style.space(10)
                Label { Layout.fillWidth: true; text: "Save an arrangement, then say its name to return to it."; color: Color.muted }
                RowLayout {
                    Field { id: bookmarkName; Layout.fillWidth: true; placeholderText: "New bookmark, e.g. work mode" }
                    Button { text: "Save desktop"; enabled: !backend.busy && !root.dirty && bookmarkName.text.trim() !== ""; onClicked: root.book("save", bookmarkName.text.trim()) }
                }
                Item {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    ColumnLayout {
                        anchors { left: parent.left; right: parent.right; top: parent.top }
                        spacing: Style.space(10)
                        Label { visible: bookmarkPager.count === 0; text: "No bookmarks saved yet."; color: Color.muted }
                        Repeater {
                            model: backend.document ? backend.document.bookmarks.slice(bookmarkPager.first, bookmarkPager.end) : []
                            ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true; spacing: Style.space(10)
                                Label { Layout.fillWidth: true; text: modelData.name + " · " + modelData.windows + " windows"; font.bold: true; maximumLineCount: 2; elide: Text.ElideRight }
                                RowLayout {
                                    Button { text: "Restore"; enabled: !backend.busy && !root.dirty; onClicked: root.book("restore", modelData.name, modelData.revision) }
                                    Button { text: root.deleteName === "save:" + modelData.name ? "Replace now" : "Update desktop"; enabled: !backend.busy && !root.dirty; onClicked: { if (root.deleteName === "save:" + modelData.name) { root.book("save", modelData.name, modelData.revision); root.deleteName = "" } else root.deleteName = "save:" + modelData.name } }
                                    Button { text: root.deleteName === modelData.name ? "Delete now" : "Delete"; enabled: !backend.busy && !root.dirty; onClicked: { if (root.deleteName === modelData.name) { root.book("delete", modelData.name, modelData.revision); root.deleteName = "" } else root.deleteName = modelData.name } }
                                }
                                Label { text: "Other phrases, separated by commas"; color: Color.muted }
                                Field { id: bookmarkPhrases; Layout.fillWidth: true; text: root.bookmarkEdits[modelData.name] !== undefined ? root.bookmarkEdits[modelData.name] : modelData.phrases.join(", "); onTextEdited: root.bookmarkEdits[modelData.name] = text }
                                Button { text: "Save bookmark phrases"; enabled: !backend.busy && !root.dirty; onClicked: root.book("phrases", modelData.name, modelData.revision, {phrases: bookmarkPhrases.text.split(",").map(function(p) { return p.trim() }).filter(function(p) { return p !== "" })}) }
                            }
                        }
                    }
                }
                Pager { id: bookmarkPager; objectName: "bookmarkPager"; Layout.fillWidth: true; count: backend.document ? backend.document.bookmarks.length : 0; unit: "bookmarks" }
            }
            HistoryView { anchors.fill: parent; visible: root.page === "History"; backend: root.backend }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Color.popups.border }
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; color: Color.muted; text: root.dirty ? "Unsaved settings" : root.page === "History" ? "History stays on this computer" : "Shift+F10 pause wake · Ctrl+F10 cancel"; maximumLineCount: 2; elide: Text.ElideRight }
            Button { text: "Save and apply"; visible: root.page !== "History" || root.dirty; primary: true; enabled: root.draft !== null && !backend.busy && root.dirty; onClicked: root.save() }
        }
    }
}
