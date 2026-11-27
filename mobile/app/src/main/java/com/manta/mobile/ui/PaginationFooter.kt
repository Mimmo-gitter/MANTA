package com.manta.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manta.mobile.core.theme.ui.DarkSurface
import com.manta.mobile.core.theme.ui.GrayText
import com.manta.mobile.core.theme.ui.Primary
import com.manta.mobile.core.theme.ui.TextLight

@Composable
fun PaginationFooter(
    modifier: Modifier = Modifier,
    currentPage: Int = 1,
    totalPages: Int = 3,
    showingRangeText: String = "Showing 1-8 of 16 alerts",
    onPageSelected: (Int) -> Unit = {}
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 16.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center
        ) {
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(DarkSurface)
                    .clickable(enabled = currentPage > 1) { onPageSelected(currentPage - 1) },
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = Icons.AutoMirrored.Filled.KeyboardArrowLeft,
                    contentDescription = "Previous Page",
                    tint = if (currentPage > 1) TextLight else GrayText
                )
            }
            (1..totalPages).forEach { page ->
                val isSelected = page == currentPage
                val containerBg = if (isSelected) Primary else DarkSurface
                val textClr = if (isSelected) Color(0xFF10131E) else TextLight

                Box(
                    modifier = Modifier
                        .padding(horizontal = 4.dp)
                        .size(36.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(containerBg)
                        .clickable { onPageSelected(page) },
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = "$page",
                        style = MaterialTheme.typography.titleMedium,
                        color = textClr,
                        fontWeight = FontWeight.Bold,
                        fontSize = 14.sp
                    )
                }
            }
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(DarkSurface)
                    .clickable(enabled = currentPage < totalPages) {
                        onPageSelected(currentPage + 1)
                    },
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
                    contentDescription = "Next Page",
                    tint = if (currentPage < totalPages) TextLight else GrayText
                )
            }
        }
        Spacer(modifier = Modifier.height(8.dp))
        Text(
            text = showingRangeText,
            style = MaterialTheme.typography.bodySmall,
            color = GrayText
        )
    }
}
