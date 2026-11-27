package com.manta.mobile.core.network

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.Path

@Serializable
data class ProjectItemResponse(
    val id: String = "",
    val name: String = "",
    val slug: String = "",
    @SerialName("alert_count") val alertCount: Int = 0
)

@Serializable
data class DoraAggregatesResponse(
    @SerialName("lead_time_hours") val leadTimeHours: Double? = null,
    @SerialName("deployment_frequency_per_day") val deploymentFrequencyPerDay: Double? = null,
    @SerialName("change_failure_rate_pct") val changeFailureRatePct: Double? = null,
    @SerialName("mttr_hours") val mttrHours: Double? = null
)

@Serializable
data class DoraTrendItemResponse(
    val date: String = "",
    @SerialName("lead_time_hours") val leadTimeHours: Double? = null,
    @SerialName("deployment_frequency_per_day") val deploymentFrequencyPerDay: Double? = null,
    @SerialName("change_failure_rate_pct") val changeFailureRatePct: Double? = null,
    @SerialName("mttr_hours") val mttrHours: Double? = null
)

@Serializable
data class DoraMetricsResponse(
    @SerialName("project_id") val projectId: String = "",
    val window: String = "14d",
    @SerialName("authoritative_deployment_signal") val authoritativeDeploymentSignal: String = "",
    val aggregates: DoraAggregatesResponse = DoraAggregatesResponse(),
    val notes: List<String> = emptyList(),
    val trend: List<DoraTrendItemResponse> = emptyList()
)

interface ProjectApiService {
    @GET("projects")
    suspend fun getProjects(): Response<List<ProjectItemResponse>>

    @GET("dora/projects/{project_id}")
    suspend fun getDoraMetrics(@Path("project_id") projectId: String): Response<DoraMetricsResponse>
}
