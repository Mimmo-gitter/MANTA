package com.manta.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.manta.mobile.core.theme.ui.DarkSurface
import com.manta.mobile.core.theme.ui.GrayText
import com.manta.mobile.core.theme.ui.Secondary
import com.manta.mobile.core.theme.ui.TextLight

data class FilterPillItem(val id: String, val label: String, val count: Int? = null)

@Composable
fun FilterPillRow(
    items: List<FilterPillItem>,
    selectedId: String,
    onItemSelected: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    LazyRow(
        modifier = modifier,
        contentPadding = PaddingValues(horizontal = 4.dp)
    ) {
        items(items) { pill ->
            val isSelected = pill.id == selectedId
            val containerBg = if (isSelected) Secondary else DarkSurface
            val textClr = if (isSelected) TextLight else GrayText

            Box(
                modifier = Modifier
                    .padding(end = 8.dp)
                    .clip(RoundedCornerShape(20.dp))
                    .background(containerBg)
                    .clickable { onItemSelected(pill.id) }
                    .padding(horizontal = 14.dp, vertical = 8.dp),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = if (pill.count != null) "${pill.label} ${pill.count}" else pill.label,
                    style = MaterialTheme.typography.labelMedium,
                    color = textClr,
                    fontWeight = if (isSelected) FontWeight.Bold else FontWeight.SemiBold
                )
            }
        }
    }
}
