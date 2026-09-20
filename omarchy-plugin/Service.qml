import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland
import qs.Commons

Item {
    id: root
    property var status: ({phase: "idle", enabled: true, size: 48, top: 54})
    property bool online: false
    property var connection: null
    property int retryDelay: 500
    property real receivedAt: 0
    readonly property bool showing: online && status.enabled !== false && status.phase !== "idle"
    readonly property real level: Number(status.level || 0)
    readonly property color surfaceColor: Color.popups.background
    readonly property color strokeColor: Color.popups.text
    readonly property color outlineColor: Color.popups.border
    readonly property real diameter: Math.max(28, Math.min(96, Number(status.size || 48)))
    readonly property real dictationWidth: Math.max(diameter, Math.min(480, Number(status.dictation_width || 216)))
    property real expansion: showing && (status.dictation || status.phase === "dictating") ? 1 : 0
    Behavior on expansion { NumberAnimation { duration: 260; easing.type: Easing.InOutCubic } }

    function connectBackend() {
        if (connection) return
        connection = streamComponent.createObject(root)
        connection.connected = true
    }
    function disconnectBackend(socket) {
        if (connection !== socket) return
        connection = null
        online = false
        socket.connected = false
        socket.destroy()
        Qt.callLater(function() { if (!root.connection) retry.restart() })
    }
    Component.onCompleted: connectBackend()
    Timer {
        id: retry
        interval: root.retryDelay
        onTriggered: {
            root.retryDelay = Math.min(5000, root.retryDelay * 2)
            root.connectBackend()
        }
    }
    Component {
        id: streamComponent
        Socket {
            id: socket
            path: String(Quickshell.env("XDG_RUNTIME_DIR")) + "/jev-voice/control.sock"
            parser: SplitParser {
                splitMarker: "\n"
                onRead: function(line) {
                    if (root.connection !== socket) return
                    try {
                        var next = JSON.parse(line)
                        if (!next.phase) return
                        root.status = next
                        wave.setTrace(next.waveform || [])
                        root.online = true
                        root.receivedAt = Date.now()
                        root.retryDelay = 500
                    } catch (e) {}
                }
            }
            onConnectedChanged: {
                if (connected) {
                    retry.stop()
                    write('{"action":"subscribe"}\n')
                    flush()
                } else root.disconnectBackend(socket)
            }
            onError: root.disconnectBackend(socket)
        }
    }
    Timer {
        interval: 1000
        running: root.showing
        repeat: true
        onTriggered: if (Date.now() - root.receivedAt > 40000) root.online = false
    }
    IpcHandler {
        target: "jev-voice-overlay"
        function state(): string {
            return JSON.stringify({online:root.online, visible:root.showing, phase:root.status.phase,
                size:Math.round(badge.width + 2), height:panel.height,
                expansion:root.expansion, top:panel.margins.top, frames:wave.frames,
                waveformPoints:wave.trace.length, waveformPeak:wave.peak,
                colors:{background:String(root.surfaceColor),wave:String(root.strokeColor),border:String(root.outlineColor)},
                animationRunning:animation.running})
        }
    }
    PanelWindow {
        id: panel
        visible: root.showing || badge.opacity > 0
        screen: Quickshell.screens.find(s => s.name === (Hyprland.focusedMonitor ? Hyprland.focusedMonitor.name : "")) || Quickshell.screens[0]
        anchors.top: true
        margins.top: Number(root.status.top === undefined ? 54 : root.status.top)
        // Keep the transparent, input-free surface fixed while its centred
        // shape morphs, avoiding compositor resize jitter during expansion.
        implicitWidth: root.dictationWidth
        implicitHeight: root.diameter
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.namespace: "jev-voice-indicator"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        mask: Region {}

        Rectangle {
            id: badge
            anchors.centerIn: parent
            width: root.diameter + (root.dictationWidth - root.diameter) * root.expansion - 2
            height: root.diameter - 2
            radius: height / 2
            opacity: root.showing ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 180 } }
            color: root.surfaceColor
            border.width: 1
            border.color: root.outlineColor
            Canvas {
                id: wave
                anchors.fill: parent
                property real phase: 0
                property real amplitude: (root.status.phase === "listening" || root.status.phase === "dictating") ? 1.6 + 6.8 * root.level : root.status.phase === "processing" ? 3.5 : 1.6
                property int frames: 0
                property var trace: []
                property var previousTrace: []
                property real traceAt: 0
                property real peak: 0
                function setTrace(values) {
                    previousTrace = trace
                    trace = values.slice(-160)
                    peak = trace.reduce((largest, sample) => Math.max(largest, Math.abs(sample)), 0)
                    traceAt = Date.now()
                    requestPaint()
                }
                function sample(values, index) {
                    var offset = index - (160 - values.length)
                    return offset < 0 ? 0 : Math.max(-1, Math.min(1, Number(values[offset] || 0) / 100))
                }
                Behavior on amplitude { NumberAnimation { duration: 90 } }
                onAmplitudeChanged: requestPaint()
                Connections {
                    target: root
                    function onStrokeColorChanged() { wave.requestPaint() }
                }
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()
                    ctx.strokeStyle = root.strokeColor
                    ctx.lineWidth = 1.5
                    ctx.lineCap = "round"
                    ctx.lineJoin = "round"
                    var start = 9 + 4 * root.expansion, span = width - 2 * start
                    var blend = Math.min(1, (Date.now() - traceAt) / 40)
                    var gain = height * 0.32
                    ctx.globalAlpha = root.status.phase === "processing" && root.expansion > 0.5 ? 0.65 + 0.15 * Math.sin(phase) : 1
                    ctx.beginPath()
                    for (var i = 0; i < 160; i++) {
                        var t = i / 159
                        var envelope = Math.pow(Math.sin(Math.PI * t), 1.1)
                        var sine = Math.sin(t * Math.PI * 3 - phase) * amplitude * envelope
                        var measured = sample(previousTrace, i) * (1 - blend) + sample(trace, i) * blend
                        var edge = Math.min(1, t * 18, (1 - t) * 18)
                        var y = height / 2 + sine * (1 - root.expansion) + measured * gain * edge * root.expansion
                        if (i === 0) ctx.moveTo(start + t * span, y)
                        else ctx.lineTo(start + t * span, y)
                    }
                    ctx.stroke()
                }
                Timer {
                    id: animation
                    interval: 33
                    running: panel.visible
                    repeat: true
                    onTriggered: {
                        wave.phase += root.status.phase === "processing" ? 0.18 : 0.12
                        wave.frames++
                        wave.requestPaint()
                    }
                }
            }
        }
    }
}
