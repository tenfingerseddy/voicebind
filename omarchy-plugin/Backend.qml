import QtQuick
import Quickshell
import Quickshell.Io

Item {
    id: root
    property var status: ({phase: "idle", wake_enabled: true})
    property bool online: false
    property var document: null
    property var history: []
    property bool historyLoaded: false
    property bool historyPending: false
    property string notice: ""
    property bool failed: false
    property bool busy: worker.running
    property var connection: null
    signal loaded()
    function refreshHistory() {
        if (busy) { historyPending = true; return }
        request({action: "history"})
    }
    onBusyChanged: {
        if (!busy && historyPending) {
            historyPending = false
            Qt.callLater(function() { root.refreshHistory() })
        }
    }

    function request(value) {
        if (busy) return
        notice = ""; failed = false
        worker.payload = JSON.stringify(value)
        worker.running = true
    }
    function control(action) {
        var socket = commandComponent.createObject(root, {action: action})
        socket.connected = true
    }
    function connectBackend() {
        if (connection) return
        connection = streamComponent.createObject(root)
        connection.connected = true
    }
    function disconnected(socket) {
        if (connection !== socket) return
        connection = null; online = false
        socket.connected = false; socket.destroy()
        Qt.callLater(function() { if (!root.connection) retry.restart() })
    }
    Component.onCompleted: connectBackend()
    Timer { id: retry; interval: 1500; onTriggered: root.connectBackend() }
    Component {
        id: streamComponent
        Socket {
            id: socket
            path: String(Quickshell.env("XDG_RUNTIME_DIR")) + "/jev-voice/control.sock"
            onConnectedChanged: {
                if (connected) { retry.stop(); write('{"action":"subscribe"}\n'); flush() }
                else root.disconnected(socket)
            }
            onError: root.disconnected(socket)
            parser: SplitParser {
                splitMarker: "\n"
                onRead: function(line) {
                    try { var value = JSON.parse(line); if (value.phase) { root.status = value; root.online = true } } catch(e) {}
                }
            }
        }
    }
    Component {
        id: commandComponent
        Socket {
            id: socket
            property string action: ""
            path: String(Quickshell.env("XDG_RUNTIME_DIR")) + "/jev-voice/control.sock"
            onConnectedChanged: if (connected) { write(JSON.stringify({action: action}) + "\n"); flush() }
            onError: { root.notice = "Listener unavailable"; root.failed = true; destroy() }
            parser: SplitParser {
                splitMarker: "\n"
                onRead: function(line) {
                    try { var r = JSON.parse(line); if (!r.ok) { root.notice = r.message; root.failed = true } } catch(e) {}
                    socket.connected = false; socket.destroy()
                }
            }
        }
    }
    Process {
        id: worker
        property string payload: ""
        command: ["sh", "-c", 'if [ -x "$HOME/.local/bin/voicebind" ] && [ "$(basename -- "$(readlink -f -- "$HOME/.local/bin/voicebind")")" = voice-control ]; then exec "$HOME/.local/bin/voicebind" panel; else printf \'{"ok":false,"message":"Complete Voicebind setup to enable listening and settings."}\\n\'; fi']
        stdinEnabled: true
        onStarted: { write(payload + "\n"); payload = "" }
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    var r = JSON.parse(text)
                    root.failed = !r.ok
                    root.notice = r.message || ""
                    if (r.document) { root.document = r.document; root.loaded() }
                    if (Array.isArray(r.history)) { root.history = r.history; root.historyLoaded = true }
                } catch(e) { root.failed = true; root.notice = "Could not load voice settings." }
            }
        }
        onExited: function(code) { if (code !== 0) { root.failed = true; root.notice = "Voice settings helper did not complete." } }
    }
}
