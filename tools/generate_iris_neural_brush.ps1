Add-Type -AssemblyName System.Drawing

$outputDir = Join-Path $PSScriptRoot "..\assets\textures\iris_stockings"
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

function New-Canvas {
    $bitmap = New-Object System.Drawing.Bitmap 1024, 1024
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.Color]::Black)
    return @($bitmap, $graphics)
}

function New-WhitePen([float]$width) {
    $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::White), $width
    $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    return $pen
}

function Draw-Diamond($graphics, $pen, [float]$x, [float]$y, [float]$radius) {
    $points = @(
        (New-Object System.Drawing.PointF $x, ($y - $radius)),
        (New-Object System.Drawing.PointF ($x + $radius), $y),
        (New-Object System.Drawing.PointF $x, ($y + $radius)),
        (New-Object System.Drawing.PointF ($x - $radius), $y),
        (New-Object System.Drawing.PointF $x, ($y - $radius))
    )
    $graphics.DrawLines($pen, $points)
}

function Draw-Node($graphics, [float]$x, [float]$y, [float]$radius) {
    $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
    $graphics.FillEllipse($brush, ($x - $radius), ($y - $radius), ($radius * 2), ($radius * 2))
    $brush.Dispose()
}

$canvas = New-Canvas
$bitmap = $canvas[0]
$graphics = $canvas[1]
$mainPen = New-WhitePen 15
$branchPen = New-WhitePen 11

$graphics.DrawLine($mainPen, 512, 0, 512, 135)
$graphics.DrawLine($mainPen, 512, 215, 512, 390)
$graphics.DrawLine($mainPen, 512, 470, 512, 650)
$graphics.DrawLine($mainPen, 512, 730, 512, 1024)
Draw-Diamond $graphics $mainPen 512 175 40
Draw-Diamond $graphics $mainPen 512 430 40
Draw-Diamond $graphics $mainPen 512 690 40

$graphics.DrawLines($branchPen, @(
    (New-Object System.Drawing.PointF 512, 285),
    (New-Object System.Drawing.PointF 425, 335),
    (New-Object System.Drawing.PointF 350, 335)
))
$graphics.DrawLines($branchPen, @(
    (New-Object System.Drawing.PointF 512, 545),
    (New-Object System.Drawing.PointF 600, 595),
    (New-Object System.Drawing.PointF 680, 595)
))
$graphics.DrawLines($branchPen, @(
    (New-Object System.Drawing.PointF 512, 825),
    (New-Object System.Drawing.PointF 425, 875),
    (New-Object System.Drawing.PointF 350, 875)
))
Draw-Node $graphics 350 335 14
Draw-Node $graphics 680 595 14
Draw-Node $graphics 350 875 14

$pathFile = Join-Path $outputDir "IRIS_NeuralBrush_Path.png"
$bitmap.Save($pathFile, [System.Drawing.Imaging.ImageFormat]::Png)
$mainPen.Dispose()
$branchPen.Dispose()
$graphics.Dispose()
$bitmap.Dispose()

$canvas = New-Canvas
$bitmap = $canvas[0]
$graphics = $canvas[1]
$mainPen = New-WhitePen 16
$branchPen = New-WhitePen 12

$graphics.DrawLine($mainPen, 512, 0, 512, 320)
Draw-Diamond $graphics $mainPen 512 440 120
$graphics.DrawLine($mainPen, 512, 560, 512, 1024)
$graphics.DrawLine($branchPen, 430, 360, 275, 205)
$graphics.DrawLine($branchPen, 594, 360, 749, 205)
$graphics.DrawLine($branchPen, 430, 520, 275, 675)
$graphics.DrawLine($branchPen, 594, 520, 749, 675)
Draw-Node $graphics 275 205 16
Draw-Node $graphics 749 205 16
Draw-Node $graphics 275 675 16
Draw-Node $graphics 749 675 16

$junctionFile = Join-Path $outputDir "IRIS_NeuralBrush_Junction.png"
$bitmap.Save($junctionFile, [System.Drawing.Imaging.ImageFormat]::Png)
$mainPen.Dispose()
$branchPen.Dispose()
$graphics.Dispose()
$bitmap.Dispose()

Write-Output $pathFile
Write-Output $junctionFile
