import { useAnalyticsExceptions, useDaily, useExceptions, useFunnel } from './lib/queries'

export default function App() {
  const funnel = useFunnel()
  const daily = useDaily()
  const analytics = useAnalyticsExceptions()
  const exceptions = useExceptions()

  return (
    <div style={{ padding: 24 }}>
      <div className="card">
        <div className="card-head">
          <div className="title">API client smoke test</div>
        </div>
        <div className="card-body">
          <div className="kv-row">
            <div className="k">funnel</div>
            <div className="v" id="probe-funnel">
              {funnel.data ? `${funnel.data.ingested} ingested / ${funnel.data.exceptions} exceptions` : String(funnel.error ?? 'loading')}
            </div>
          </div>
          <div className="kv-row">
            <div className="k">daily</div>
            <div className="v" id="probe-daily">
              {daily.data ? `${daily.data.count} days` : String(daily.error ?? 'loading')}
            </div>
          </div>
          <div className="kv-row">
            <div className="k">analytics</div>
            <div className="v" id="probe-analytics">
              {analytics.data ? `${analytics.data.by_cause.length} causes / risk ${analytics.data.value_at_risk_paise}` : String(analytics.error ?? 'loading')}
            </div>
          </div>
          <div className="kv-row">
            <div className="k">exceptions</div>
            <div className="v" id="probe-exceptions">
              {exceptions.data ? `${exceptions.data.count} rows` : String(exceptions.error ?? 'loading')}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
