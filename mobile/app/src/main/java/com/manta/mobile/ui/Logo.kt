package com.manta.mobile.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import com.manta.mobile.R

@Composable
fun Logo(
    modifier: Modifier = Modifier
        .size(100.dp)
) {
    Image(
        painter = painterResource(id = R.drawable.manta_logo),
        contentDescription = "M.A.N.T.A. Logo",
        modifier = modifier
    )
}