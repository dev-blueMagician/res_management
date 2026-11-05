import QtQuick
import QtQuick.Controls as Controls

// Every label in the app. Importing this folder after QtQuick.Controls makes "Label" mean
// this one. Qt's default treats text that looks like markup as rich text, so a stored name
// or note could hide words, forge lines or fetch an image over the network (NFR-1).
Controls.Label {
    textFormat: Text.PlainText
}
