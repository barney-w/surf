// ---------------------------------------------------------------------------
// Module: alerts.bicep
// Project: Surf — Multi-Agent Orchestration Platform
// Description: Metric alert rules for container app health monitoring.
// ---------------------------------------------------------------------------

@description('Azure region for alert rule resources (reserved for future scheduled query rules)')
#disable-next-line no-unused-params
param location string

@description('Resource ID of the container app to monitor')
param containerAppResourceId string

@description('Resource ID of the Log Analytics workspace (reserved for future log-based alert rules)')
#disable-next-line no-unused-params
param logAnalyticsWorkspaceId string

@description('Resource ID of the action group for alert notifications (empty to skip notifications)')
param actionGroupId string = ''

// ---------------------------------------------------------------------------
// Alert: Container restart count > 0 in 5 minutes
// ---------------------------------------------------------------------------

resource restartAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-container-restarts'
  location: 'global'
  properties: {
    description: 'Fires when the container app restarts within a 5-minute window.'
    severity: 2
    enabled: true
    scopes: [
      containerAppResourceId
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'RestartCount'
          metricName: 'RestartCount'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: empty(actionGroupId) ? [] : [
      {
        actionGroupId: actionGroupId
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Alert: 5xx response rate > 5% in 5 minutes
// ---------------------------------------------------------------------------

resource fiveXxAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-5xx-response-rate'
  location: 'global'
  properties: {
    description: 'Fires when the 5xx response rate exceeds 5% over a 5-minute window.'
    severity: 2
    enabled: true
    scopes: [
      containerAppResourceId
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'Requests5xx'
          metricName: 'Requests'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 5
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
          dimensions: [
            {
              name: 'statusCodeCategory'
              operator: 'Include'
              values: [
                '5xx'
              ]
            }
          ]
        }
      ]
    }
    actions: empty(actionGroupId) ? [] : [
      {
        actionGroupId: actionGroupId
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Alert: CPU usage > 80% for 5 minutes
// ---------------------------------------------------------------------------

resource cpuAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-cpu-high'
  location: 'global'
  properties: {
    description: 'Fires when container app CPU usage exceeds 80% for 5 minutes.'
    severity: 2
    enabled: true
    scopes: [
      containerAppResourceId
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'CpuUsage'
          metricName: 'UsageNanoCores'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 800000000
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: empty(actionGroupId) ? [] : [
      {
        actionGroupId: actionGroupId
      }
    ]
  }
}
