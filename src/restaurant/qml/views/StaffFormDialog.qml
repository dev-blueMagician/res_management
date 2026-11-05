import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

// FR-S3: create a staff member, or edit an existing one's name and role.
FormDialog {
    id: root

    property int staffId: 0
    property string originalRole: ""
    readonly property bool isSelf: staffId !== 0 && staffId === api.session.user.id

    signal saved(string message)

    function _useRoles(roles, value) {
        role.model = roles
        role.currentIndex = 0
        if (value)
            role.selectValue(value)
    }

    function openNew(roles) {
        staffId = 0
        originalRole = ""
        fullName.text = ""
        username.text = ""
        password.text = ""
        confirmation.text = ""
        _useRoles(roles, "cashier")
        open()
    }

    function openEdit(record, roles) {
        staffId = record.id
        originalRole = record.role
        fullName.text = record.full_name
        username.text = record.username
        _useRoles(roles, record.role)
        open()
    }

    function save() {
        var result = staffId === 0
                ? api.staff.createStaff(username.text, fullName.text, role.currentValue,
                                        password.text, confirmation.text)
                : api.staff.updateStaff(staffId, { full_name: fullName.text, role: role.currentValue })
        if (!result.ok) {
            errorText = result.error
            return
        }
        close()
        saved(result.data.full_name + " was saved.")
    }

    title: staffId === 0 ? "New staff member" : "Edit staff member"
    confirmEnabled: fullName.text.trim() !== "" && username.text.trim() !== ""
    onOpened: fullName.focusInput()
    onClosed: {
        password.text = ""
        confirmation.text = ""
    }
    onSubmitted: save()

    AppTextField {
        id: fullName
        Layout.fillWidth: true
        label: "Full name"
        onAccepted: {
            if (root.staffId === 0)
                username.focusInput()
            else if (root.confirmEnabled)
                root.save()
        }
    }

    AppTextField {
        id: username
        Layout.fillWidth: true
        label: root.staffId === 0 ? "Username" : "Username (can't be changed)"
        readOnly: root.staffId !== 0
        onAccepted: password.focusInput()
    }

    AppComboBox {
        id: role
        Layout.fillWidth: true
        label: "Role"
        textRole: "label"
        valueRole: "value"
        model: []
    }

    MessageBanner {
        Layout.fillWidth: true
        kind: "warning"
        text: root.isSelf && role.currentValue !== root.originalRole
              ? "This is your own account. Changing your role signs you out." : ""
    }

    AppTextField {
        id: password
        Layout.fillWidth: true
        visible: root.staffId === 0
        label: "Password (at least 8 characters)"
        echoMode: TextInput.Password
        onAccepted: confirmation.focusInput()
    }

    AppTextField {
        id: confirmation
        Layout.fillWidth: true
        visible: root.staffId === 0
        label: "Password again"
        echoMode: TextInput.Password
        onAccepted: {
            if (root.confirmEnabled)
                root.save()
        }
    }
}
