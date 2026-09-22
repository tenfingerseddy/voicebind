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
        var next = JSON.parse(JSON.stringify(backend.document.config))
        next.interpretation = Object.assign({reject_low_confidence: false, minimum_confidence: 60}, next.interpretation || {})
        draft = next
        phrases.clear(); aliases.clear()
        Object.keys(draft.phrases || {}).forEach(function(k) { phrases.append({spoken: k, command: draft.phrases[k]}) })
        var apps = Object.assign({}, backend.document.roles, draft.apps || {})
        Object.keys(apps).forEach(function(k) { aliases.append({spoken: k, app: apps[k]}) })
        wake.text = draft.voice.wake_phrase
        silence.text = String(draft.recognition.end_silence_ms)
        confidence.text = String(draft.interpretation.minimum_confidence)
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
        cfg.interpretation.minimum_confidence = Number(confidence.text)
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
        spacing: Style.space(12)
        RowLayout {
            spacing: Style.space(12)
            Icon { Layout.preferredWidth: Style.space(32); Layout.preferredHeight: Style.space(32); foreground: Color.accent; paused: !backend.online || !backend.status.wake_enabled }
            ColumnLayout {
                Layout.fillWidth: true; spacing: Style.space(3)
                Label { text: "Voicebind"; font.pixelSize: Style.font.heading; font.bold: true }
                Label {
                    Layout.fillWidth: true
                    text: !backend.online ? "Listener stopped" : backend.status.phase === "idle" ? (backend.status.wake_enabled ? "Ready · “" + (root.draft ? root.draft.voice.wake_phrase : "computer") + "” or F10" : "Wake paused · hold F10") : backend.status.phase.charAt(0).toUpperCase() + backend.status.phase.slice(1)
                    color: secondaryColor; font.pixelSize: Style.font.caption || Style.font.body
                    maximumLineCount: 1; elide: Text.ElideRight
                }
            }
            Button { text: backend.online ? (backend.status.wake_enabled ? "Pause" : "Resume") : "Start"; Accessible.name: backend.online ? "Toggle wake listening" : "Start listener"; enabled: !backend.busy && root.draft !== null; onClicked: backend.online ? backend.control("toggle-wake") : backend.request({action: "start"}) }
            Button { text: "Cancel"; visible: backend.online && backend.status.phase !== "idle"; onClicked: backend.control("cancel") }
        }
        Label { Layout.fillWidth: true; visible: backend.status.phase === "waiting" && !!backend.status.prompt; text: backend.status.prompt || ""; color: Color.accent }
        Item {
            Layout.fillWidth: true
            implicitHeight: navigation.implicitHeight
            visible: root.draft !== null
            Divider { anchors { left: parent.left; right: parent.right; bottom: parent.bottom } }
            RowLayout {
                id: navigation
                anchors.fill: parent; spacing: Style.space(4)
                Repeater {
                    model: ["Voice", "Phrases", "Apps", "Bookmarks", "History"]
                    Tab { required property string modelData; Layout.fillWidth: true; text: modelData; selected: root.page === modelData; onClicked: root.page = modelData }
                }
            }
        }
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
                Label { Layout.fillWidth: true; text: "Setup downloads the English speech model (148 MB). A Jev API key is optional."; color: secondaryColor }
                RowLayout {
                    Button { text: "Setup guide"; primary: true; onClicked: Qt.openUrlExternally("https://github.com/tenfingerseddy/voicebind#install") }
                    Button { text: "Retry"; onClicked: backend.request({action: "panel"}) }
                }
            }
            ColumnLayout {
                anchors.fill: parent
                visible: root.page === "Voice" && root.draft !== null
                spacing: Style.space(12)
                RowLayout {
                    spacing: Style.space(4)
                    Repeater {
                        model: ["Listening", "Appearance", "Jev"]
                        Tab { required property string modelData; text: modelData; secondary: true; selected: root.voicePage === modelData; onClicked: root.voicePage = modelData }
                    }
                }
                ColumnLayout {
                    visible: root.voicePage === "Listening"
                    Layout.fillWidth: true
                    spacing: Style.space(8)
                    GridLayout {
                        Layout.fillWidth: true; columns: 2; columnSpacing: Style.space(20); rowSpacing: Style.space(8)
                        Label { text: "Wake phrase" }
                        Field { id: wake; objectName: "wake"; Layout.fillWidth: true; onTextEdited: root.dirty = true }
                        Label { text: "Microphone" }
                        Select { id: microphone; objectName: "microphone"; Layout.fillWidth: true; model: backend.document ? backend.document.microphones : []; textRole: "description"; onActivated: root.dirty = true }
                        Label { text: "Silence wait" }
                        RowLayout {
                            Field { id: silence; objectName: "silence"; Layout.preferredWidth: Style.space(78); validator: IntValidator { bottom: 200; top: 1500 } onTextEdited: root.dirty = true }
                            Label { text: "ms"; color: secondaryColor }
                            Item { Layout.fillWidth: true }
                        }
                    }
                    Label { Layout.fillWidth: true; text: "200–1500 ms. Lower is faster; higher allows pauses. F10 runs on release."; color: secondaryColor; font.pixelSize: Style.font.caption || Style.font.body }
                    Divider { Layout.fillWidth: true; Layout.topMargin: Style.space(4); Layout.bottomMargin: Style.space(4) }
                    Toggle {
                        Layout.fillWidth: true; text: "Wake on startup"; description: "Listen for your wake phrase when Voicebind starts"
                        checkedValue: root.draft ? root.draft.voice.wake_enabled : false
                        onClicked: { root.draft.voice.wake_enabled = !root.draft.voice.wake_enabled; root.draft = Object.assign({}, root.draft); root.dirty = true }
                    }
                    Toggle {
                        Layout.fillWidth: true; text: "Follow apps"; description: "Focus apps when you open or move them"
                        checkedValue: root.draft ? root.draft.desktop.focus_by_default : false
                        onClicked: { root.draft.desktop.focus_by_default = !root.draft.desktop.focus_by_default; root.draft = Object.assign({}, root.draft); root.dirty = true }
                    }
                    Divider { Layout.fillWidth: true; Layout.topMargin: Style.space(4); Layout.bottomMargin: Style.space(4) }
                    RowLayout {
                        Label { Layout.fillWidth: true; text: "Dictate: “" + (root.draft ? root.draft.voice.wake_phrase : "computer") + " dictate”\nFinish: “" + (root.draft ? root.draft.voice.wake_phrase : "computer") + " finish”"; color: secondaryColor; font.pixelSize: Style.font.caption || Style.font.body }
                        Button { text: "Stop listener"; quiet: true; visible: backend.online; enabled: !backend.busy && !root.dirty; onClicked: backend.request({action: "stop"}) }
                    }
                }
                ColumnLayout {
                    visible: root.voicePage === "Appearance"
                    Layout.fillWidth: true; spacing: Style.space(12)
                    Toggle { Layout.fillWidth: true; text: "Listening indicator"; description: "A circle for commands, a waveform for dictation"; checkedValue: root.draft ? root.draft.indicator.enabled : false; onClicked: { root.draft.indicator.enabled = !root.draft.indicator.enabled; root.draft = Object.assign({}, root.draft); root.dirty = true } }
                    Divider { Layout.fillWidth: true }
                    GridLayout {
                        Layout.fillWidth: true; columns: 2; columnSpacing: Style.space(14); rowSpacing: Style.space(10)
                        Label { text: "Circle size (px)" }
                        Field { id: size; objectName: "size"; Layout.fillWidth: true; validator: IntValidator { bottom: 28; top: 96 } onTextEdited: root.dirty = true }
                        Label { text: "Distance from top (px)" }
                        Field { id: top; objectName: "top"; Layout.fillWidth: true; validator: IntValidator { bottom: 0; top: 240 } onTextEdited: root.dirty = true }
                        Label { text: "Dictation pill width (px)" }
                        Field { id: pill; objectName: "pill"; Layout.fillWidth: true; validator: IntValidator { bottom: 120; top: 480 } onTextEdited: root.dirty = true }
                    }
                    Label { Layout.fillWidth: true; text: "Colours and type follow your Omarchy theme. The indicator sits at the top centre of your screen."; color: secondaryColor }
                }
                ColumnLayout {
                    visible: root.voicePage === "Jev"
                    Layout.fillWidth: true; spacing: Style.space(10)
                    RowLayout {
                        Label { Layout.fillWidth: true; text: "JEV INTERPRETATION"; font.bold: true; font.pixelSize: Style.font.caption || Style.font.body; color: secondaryColor }
                        Label { text: backend.document && backend.document.key_configured ? "Key saved" : "Optional"; color: backend.document && backend.document.key_configured ? Color.accent : secondaryColor; font.pixelSize: Style.font.caption || Style.font.body }
                    }
                    Label { Layout.fillWidth: true; text: "Jev turns natural speech into desktop actions. With a key, Jev interprets commands first; Whisper stays local."; color: secondaryColor }
                    Field { id: apiKey; objectName: "apiKey"; Layout.fillWidth: true; echoMode: TextInput.Password; placeholderText: backend.document && backend.document.key_configured ? "API key · leave blank to keep current key" : "Paste your Jev API key"; onTextEdited: root.dirty = true }
                    Label { Layout.fillWidth: true; text: "Your key is stored privately on this computer."; color: secondaryColor; font.pixelSize: Style.font.caption || Style.font.body }
                    Divider { Layout.fillWidth: true; Layout.topMargin: Style.space(6); Layout.bottomMargin: Style.space(6) }
                    Toggle {
                        objectName: "rejectLowConfidence"
                        Layout.fillWidth: true
                        text: "Reject low confidence"
                        description: "Skip commands when Jev is uncertain"
                        checkedValue: root.draft ? root.draft.interpretation.reject_low_confidence : false
                        onClicked: {
                            root.draft.interpretation.reject_low_confidence = !root.draft.interpretation.reject_low_confidence
                            root.draft = Object.assign({}, root.draft)
                            root.dirty = true
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: "Minimum confidence"; opacity: confidence.enabled ? 1 : 0.4 }
                        Field {
                            id: confidence; objectName: "minimumConfidence"
                            Layout.preferredWidth: Style.space(70)
                            enabled: root.draft && root.draft.interpretation.reject_low_confidence
                            validator: IntValidator { bottom: 0; top: 100 }
                            onTextEdited: root.dirty = true
                        }
                        Label { text: "%"; color: secondaryColor; opacity: confidence.enabled ? 1 : 0.4 }
                    }
                    Label { Layout.fillWidth: true; text: "Off uses the best supported interpretation. This threshold applies to Jev, not Whisper accuracy."; color: secondaryColor; font.pixelSize: Style.font.caption || Style.font.body }
                }
                Item { Layout.fillHeight: true }
            }
            ColumnLayout {
                anchors.fill: parent
                visible: root.page === "Phrases"
                spacing: Style.space(10)
                Label { Layout.fillWidth: true; text: "Your words, your shortcuts. Leave out your wake phrase."; color: secondaryColor }
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
                                Label { text: "WHEN I SAY"; font.pixelSize: Style.font.caption || Style.font.body; color: secondaryColor; font.bold: true }
                                RowLayout {
                                    Field { Layout.fillWidth: true; objectName: "phrase-" + modelData.sourceIndex; text: modelData.row.spoken; placeholderText: "What you say, e.g. get to work"; onTextEdited: { phrases.setProperty(modelData.sourceIndex, "spoken", text); root.dirty = true } }
                                    Button { text: "−"; quiet: true; Accessible.name: "Remove phrase"; onClicked: { root.dirty = true; phrases.remove(modelData.sourceIndex) } }
                                }
                                Label { text: "RUN THIS COMMAND"; font.pixelSize: Style.font.caption || Style.font.body; color: secondaryColor; font.bold: true }
                                Field { Layout.fillWidth: true; objectName: "command-" + modelData.sourceIndex; text: modelData.row.command; placeholderText: "open teams on workspace 5 then make it full screen"; onTextEdited: { phrases.setProperty(modelData.sourceIndex, "command", text); root.dirty = true } }
                                Divider { Layout.fillWidth: true; Layout.topMargin: Style.space(4) }
                            }
                        }
                        Label { Layout.fillWidth: true; visible: phrases.count === 0; text: "Add your first phrase, e.g. “write this” → “dictate”."; color: secondaryColor }
                    }
                }
                RowLayout {
                    Pager { id: phrasePager; objectName: "phrasePager"; Layout.fillWidth: true; count: phrases.count; pageSize: Math.max(1, Math.min(3, Math.floor((body.height - Style.space(100)) / Style.space(136)))) }
                    Button { text: "+ Add phrase"; onClicked: { phrases.append({spoken: "", command: ""}); phrasePager.showLast(); root.dirty = true } }
                }
            }
            ColumnLayout {
                anchors.fill: parent
                visible: root.page === "Apps"
                spacing: Style.space(10)
                Label { Layout.fillWidth: true; text: "Give your apps everyday names like browser, files or email."; color: secondaryColor }
                RowLayout {
                    Label { Layout.preferredWidth: Style.space(140); text: "SPOKEN NAME"; font.pixelSize: Style.font.caption || Style.font.body; color: secondaryColor; font.bold: true }
                    Label { Layout.fillWidth: true; text: "APPLICATION"; font.pixelSize: Style.font.caption || Style.font.body; color: secondaryColor; font.bold: true }
                }
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
                                Button { text: "−"; quiet: true; Accessible.name: "Remove app name"; onClicked: { root.dirty = true; aliases.remove(modelData.sourceIndex) } }
                            }
                        }
                    }
                }
                RowLayout {
                    Pager { id: appPager; objectName: "appPager"; Layout.fillWidth: true; count: aliases.count; pageSize: Math.max(1, Math.floor((body.height - Style.space(128)) / Style.space(46))) }
                    Button { text: "+ Add name"; enabled: backend.document && backend.document.apps.length > 0; onClicked: { aliases.append({spoken: "", app: backend.document.apps[0].id}); appPager.showLast(); root.dirty = true } }
                }
            }
            ColumnLayout {
                anchors.fill: parent
                visible: root.page === "Bookmarks"
                spacing: Style.space(10)
                Label { Layout.fillWidth: true; text: "Save an arrangement, then say its name to return to it."; color: secondaryColor }
                RowLayout {
                    Field { id: bookmarkName; Layout.fillWidth: true; placeholderText: "New bookmark, e.g. work mode" }
                    Button { text: "Save desktop"; enabled: !backend.busy && !root.dirty && bookmarkName.text.trim() !== ""; onClicked: root.book("save", bookmarkName.text.trim()) }
                }
                Item {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    ColumnLayout {
                        anchors { left: parent.left; right: parent.right; top: parent.top }
                        spacing: Style.space(10)
                        Label { visible: bookmarkPager.count === 0; text: "No bookmarks saved yet."; color: secondaryColor }
                        Repeater {
                            model: backend.document ? backend.document.bookmarks.slice(bookmarkPager.first, bookmarkPager.end) : []
                            ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true; spacing: Style.space(10)
                                Divider { Layout.fillWidth: true; Layout.topMargin: Style.space(4); Layout.bottomMargin: Style.space(4) }
                                RowLayout {
                                    Label { Layout.fillWidth: true; text: modelData.name; font.bold: true; font.pixelSize: Style.font.heading; maximumLineCount: 2; elide: Text.ElideRight }
                                    Label { text: modelData.windows + " windows"; color: secondaryColor; font.pixelSize: Style.font.caption || Style.font.body }
                                }
                                RowLayout {
                                    Button { text: "Restore"; primary: true; enabled: !backend.busy && !root.dirty; onClicked: root.book("restore", modelData.name, modelData.revision) }
                                    Button { text: root.deleteName === "save:" + modelData.name ? "Replace now" : "Update desktop"; enabled: !backend.busy && !root.dirty; onClicked: { if (root.deleteName === "save:" + modelData.name) { root.book("save", modelData.name, modelData.revision); root.deleteName = "" } else root.deleteName = "save:" + modelData.name } }
                                    Button { text: root.deleteName === modelData.name ? "Delete now" : "Delete"; quiet: true; enabled: !backend.busy && !root.dirty; onClicked: { if (root.deleteName === modelData.name) { root.book("delete", modelData.name, modelData.revision); root.deleteName = "" } else root.deleteName = modelData.name } }
                                }
                                Label { text: "Other phrases, separated by commas"; color: secondaryColor }
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
        Divider { Layout.fillWidth: true }
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; color: root.dirty ? Color.accent : secondaryColor; text: root.dirty ? "Unsaved changes" : root.page === "History" ? "Stored on this computer" : "Shift+F10 pause · Ctrl+F10 cancel"; font.pixelSize: Style.font.caption || Style.font.body; maximumLineCount: 2; elide: Text.ElideRight }
            Button { text: "Save and apply"; visible: root.page !== "History" || root.dirty; primary: true; enabled: root.draft !== null && !backend.busy && root.dirty; onClicked: root.save() }
        }
    }
}
