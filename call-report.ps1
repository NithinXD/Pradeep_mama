param(
    [string]$Uri = $(if ($env:REPORT_API_URL) { $env:REPORT_API_URL } else { "http://127.0.0.1:8000/reports/pdf" }),
    [string]$OutFile = "performance-report.pdf"
)

$payload = @{
    report_id = "123456"
    report_date = "20 March 2026, 16:00 IST"
    name = "Aarav Sharma"
    profile_pic = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2Z8fQAAAAASUVORK5CYII="
    event_name = "Dance Performance"
    confidence = 5
    creativity = 5
    technique = 5
    expression = 5
    overall_impact = 5
    score_value = 4.8
    bullets = @(
        "Strong stage presence and confident delivery"
        "Clean technique with good musicality"
        "High creativity and expressive performance"
    )
}

$jsonBody = $payload | ConvertTo-Json -Depth 5

Write-Host "Posting report payload to $Uri"
Invoke-WebRequest `
    -Uri $Uri `
    -Method Post `
    -ContentType "application/json" `
    -Body $jsonBody `
    -OutFile $OutFile

Write-Host "Saved PDF to $OutFile"