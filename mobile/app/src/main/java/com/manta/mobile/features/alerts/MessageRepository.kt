package com.manta.mobile.features.alerts

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

public object MessageRepository {
    private val _latestMessage = MutableStateFlow("Waiting for Firebase message...")
    val latestMessage: StateFlow<String> = _latestMessage

    fun updateMessage(newMessageTitle: String, newMessageBody: String) {
        _latestMessage.value = "Title: $newMessageTitle, Body: $newMessageBody"
    }
}