import QtQuick
import qs.Commons
Canvas {
    id: icon
    property color foreground: Color.foreground
    property bool paused: false
    onForegroundChanged: requestPaint()
    onPausedChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        var c = getContext("2d"), d = Math.min(width, height), x = width / 2, y = height / 2
        c.reset(); c.strokeStyle = foreground; c.lineWidth = 1.4; c.lineCap = "round"
        c.globalAlpha = paused ? 0.45 : 1
        c.beginPath(); c.arc(x, y, d * 0.42, 0, Math.PI * 2); c.stroke()
        c.beginPath()
        for (var i = 0; i <= 40; i++) {
            var px = x - d * 0.29 + i / 40 * d * 0.58
            var py = y + (paused ? 0 : Math.sin(i / 40 * Math.PI * 2) * d * 0.16)
            if (i === 0) c.moveTo(px, py); else c.lineTo(px, py)
        }
        c.stroke()
    }
}
