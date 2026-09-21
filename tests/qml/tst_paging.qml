import QtQuick
import QtTest
import "../../omarchy-plugin" as Voice

TestCase {
    id: test
    name: "PopupPaging"
    width: 548; height: 600
    visible: true
    when: windowShown
    QtObject {
        id: mockBackend
        property var document: null
        property var status: ({phase: "idle", wake_enabled: true})
        property bool online: true
        property bool busy: false
        property bool failed: false
        property string notice: ""
        property var history: []
        property bool historyLoaded: true
        property var requestValue: null
        signal loaded()
        function refreshHistory() {}
        function request(v) { requestValue = v }
        function control(v) {}
    }
    Component { id: panelFactory; Voice.PanelContent { width: 548; height: 600; backend: mockBackend } }
    Component { id: textFactory; Voice.TextPages { width: 350; height: 270 } }
    Component { id: selectFactory; Voice.Select { width: 300; textRole: "name" } }
    property var panel: null
    function init() {
        var phrases = {}, aliases = {}, apps = [], bookmarks = []
        for (var i = 0; i < 19; i++) { aliases["alias " + i] = "app" + i; apps.push({id: "app" + i, name: "App " + i}) }
        for (var j = 0; j < 8; j++) phrases["phrase " + j] = "open app" + j
        for (var k = 0; k < 3; k++) bookmarks.push({name: "desk " + k, windows: 2, phrases: [], revision: "abc"})
        mockBackend.document = {config: {voice: {wake_phrase: "computer", wake_enabled: true}, recognition: {end_silence_ms: 360, source: ""}, indicator: {size: 48, top: 54, dictation_width: 216, enabled: true}, desktop: {focus_by_default: true}, phrases: phrases, apps: aliases}, roles: {}, apps: apps, microphones: [{name: "", description: "System microphone"}], bookmarks: bookmarks, revision: "r1", key_configured: true}
        mockBackend.history = [{t: 1, outcome: "Completed", heard: "Open files", title: "", plan: "Open files", message: "Done", route: "local", activation: "ptt", ms: {whisper: 200, release_to_result: 300}, issue: false}]
        mockBackend.requestValue = null
        panel = createTemporaryObject(panelFactory, test)
        verify(panel !== null)
        wait(20)
    }
    function edit(field, text) { verify(field !== null); field.text = text; field.textEdited() }
    function button(item, text) {
        if (item.text === text && typeof item.clicked === "function") return item
        var children = item.children || []
        for (var i = 0; i < children.length; i++) { var result = button(children[i], text); if (result) return result }
        return null
    }
    function test_phrase_edits_survive_paging_and_save_correct_row() {
        panel.page = "Phrases"
        var pager = findChild(panel, "phrasePager")
        verify(pager.pageCount > 1)
        pager.page = 1; wait(20)
        var row = pager.first
        edit(findChild(panel, "phrase-" + row), "writing time")
        edit(findChild(panel, "command-" + row), "dictate")
        pager.page = 0; pager.page = 1; wait(20)
        compare(findChild(panel, "phrase-" + row).text, "writing time")
        panel.save()
        compare(mockBackend.requestValue.config.phrases["writing time"], "dictate")
        compare(mockBackend.requestValue.config.phrases["phrase 0"], "open app0")
        compare(Object.keys(mockBackend.requestValue.config.phrases).length, 8)
    }
    function test_add_and_remove_final_phrase_page() {
        panel.page = "Phrases"
        var pager = findChild(panel, "phrasePager")
        button(panel, "+ Add phrase").clicked(); wait(20)
        compare(pager.count, 9); compare(pager.page, pager.pageCount - 1)
        var last = findChild(panel, "phrase-8")
        verify(last !== null)
        last.parent.children[1].clicked(); wait(20)
        compare(pager.count, 8)
        verify(pager.page < pager.pageCount)
    }
    function test_alias_edit_survives_pages_and_voice_subpages() {
        panel.page = "Apps"
        var pager = findChild(panel, "appPager")
        pager.page = 1; wait(20)
        var index = pager.first
        edit(findChild(panel, "alias-" + index), "my browser")
        panel.page = "Voice"; panel.voicePage = "Appearance"; panel.voicePage = "Jev"
        edit(findChild(panel, "apiKey"), "test-only-key")
        panel.page = "Apps"; pager.page = 0; pager.page = 1; wait(20)
        compare(findChild(panel, "alias-" + index).text, "my browser")
        panel.save()
        compare(mockBackend.requestValue.config.apps["my browser"], "app" + index)
        compare(mockBackend.requestValue.api_key, "test-only-key")
    }
    function test_long_diagnostic_has_all_text_without_overflow() {
        var view = createTemporaryObject(textFactory, test)
        var source = "Heard:\n" + "a very long chained voice command ".repeat(100) + "\nResult:\n" + "x".repeat(600)
        view.text = source
        tryVerify(function() { return view.chunks.length > 1 })
        compare(view.chunks.join(""), source)
        var body = view.children[0], pager = view.children[2]
        for (var i = 0; i < view.chunks.length; i++) {
            pager.page = i; wait(1)
            verify(body.contentHeight <= body.height + 1, "Text exceeds page " + i)
        }
    }
    function test_picker_search_keeps_original_model_index() {
        var picker = createTemporaryObject(selectFactory, test, {model: mockBackend.document.apps})
        picker.popup.open(); wait(10)
        var search = picker.popup.contentItem.children[0]
        search.text = "App 18"; wait(10)
        compare(picker.popup.matches.length, 1)
        compare(picker.popup.matches[0].sourceIndex, 18)
        var activated = -1
        picker.activated.connect(function(i) { activated = i })
        search.accepted()
        compare(picker.currentIndex, 18); compare(activated, 18)
    }
    function checkBounds(item) {
        if (!item.visible) return
        if ((typeof item.clicked === "function" || item.placeholderText !== undefined || item.text !== undefined) && item.width > 0 && item.height > 0) {
            var position = item.mapToItem(panel, 0, 0)
            verify(position.x >= -1 && position.y >= -1 && position.x + item.width <= panel.width + 1 && position.y + item.height <= panel.height + 1,
                "Overflow: " + (item.objectName || item.text || item.placeholderText) + " at " + position.x + "," + position.y + " size " + item.width + "x" + item.height + " in " + panel.page + "/" + panel.voicePage)
        }
        var children = item.children || []
        for (var i = 0; i < children.length; i++) checkBounds(children[i])
    }
    function test_visible_controls_fit_without_scroll() {
        for (var h of [600, 520]) {
            panel.height = h
            for (var sub of ["Listening", "Appearance", "Jev"]) {
                panel.page = "Voice"; panel.voicePage = sub; wait(20); checkBounds(panel)
            }
            for (var page of ["Phrases", "Apps", "Bookmarks", "History"]) {
                panel.page = page; wait(20); checkBounds(panel)
            }
        }
    }
    function test_missing_backend_shows_setup_without_starting_listener() {
        mockBackend.document = null
        mockBackend.online = false
        panel.draft = null
        panel.height = 520
        wait(20)
        verify(button(panel, "Setup guide").visible)
        verify(!button(panel, "Start").enabled)
        checkBounds(panel)
        button(panel, "Retry").clicked()
        compare(mockBackend.requestValue.action, "panel")
        mockBackend.online = true
    }
    function test_bookmark_phrase_draft_survives_paging() {
        panel.page = "Bookmarks"
        var pager = findChild(panel, "bookmarkPager")
        // The phrase field follows its descriptive label in this bookmark card.
        var save = button(panel, "Save bookmark phrases")
        var fields = save.parent.children.filter(function(item) { return item.placeholderText !== undefined })
        compare(fields.length, 1)
        edit(fields[0], "work mode, working")
        pager.page = 1; pager.page = 0; wait(20)
        save = button(panel, "Save bookmark phrases")
        save.clicked()
        compare(mockBackend.requestValue.phrases.join(","), "work mode,working")
    }

}
