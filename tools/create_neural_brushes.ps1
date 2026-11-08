Add-Type -AssemblyName System.Drawing

$outputDir = Join-Path $PSScriptRoot "..\assets\textures\iris_stockings"
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

function New-Canvas {
    $bitmap = [System.Drawing.Bitmap]::new(1024, 1024)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.Color]::Black)
    return @($bitmap, $graphics)
}

function New-Pen([float]$width) {
    $pen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, $width)
    $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    return $pen
}

function Draw-Line($graphics, $pen, $x1, $y1, $x2, $y2) {
    $graphics.DrawLine($pen, [float]$x1, [float]$y1, [float]$x2, [float]$y2)
}

function Draw-Diamond($graphics, $pen, $cx, $cy, $radius) {
    $points = [System.Drawing.PointF[]]@(
        [System.Drawing.PointF]::new($cx, $cy - $radius),
        [System.Drawing.PointF]::new($cx + $radius, $cy),
        [System.Drawing.PointF]::new($cx, $cy + $radius),
        [System.Drawing.PointF]::new($cx - $radius, $cy)
    )
    $graphics.DrawPolygon($pen, $points)
}

$mainPen = New-Pen 24
$branchPen = New-Pen 16
$nodeBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)

# Long modular neural/circuit path.
$canvas = New-Canvas
$bitmap = $canvas[0]
$graphics = $canvas[1]

$spine = @(
    @(510, 50), @(510, 170), @(430, 250), @(510, 330),
    @(510, 470), @(610, 570), @(510, 670), @(510, 970)
)
for ($i = 0; $i -lt $spine.Count - 1; $i++) {
    Draw-Line $graphics $mainPen $spine[$i][0] $spine[$i][1] $spine[$i + 1][0] $spine[$i + 1][1]
}

Draw-Line $graphics $branchPen 430 250 270 250
Draw-Line $graphics $branchPen 270 250 210 190
Draw-Line $graphics $branchPen 510 470 730 470
Draw-Line $graphics $branchPen 730 470 800 390
Draw-Line $graphics $branchPen 610 570 790 650
Draw-Line $graphics $branchPen 790 650 850 650
Draw-Line $graphics $branchPen 510 790 330 790
Draw-Line $graphics $branchPen 330 790 250 870

Draw-Diamond $graphics $branchPen 510 330 48
Draw-Diamond $graphics $branchPen 510 670 62
foreach ($node in @(@(210,190), @(270,250), @(730,470), @(800,390), @(850,650), @(250,870), @(510,50), @(510,970))) {
    $graphics.FillEllipse($nodeBrush, $node[0] - 14, $node[1] - 14, 28, 28)
}

$pathFile = Join-Path $outputDir "IRIS_NeuralBrush_Path.png"
$bitmap.Save($pathFile, [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()

# Compact junction stamp for intersections and focal points.
$canvas = New-Canvas
$bitmap = $canvas[0]
$graphics = $canvas[1]

Draw-Diamond $graphics $mainPen 512 512 150
Draw-Diamond $graphics $branchPen 512 512 72
Draw-Line $graphics $mainPen 512 50 512 362
Draw-Line $graphics $mainPen 512 662 512 974
Draw-Line $graphics $branchPen 50 512 362 512
Draw-Line $graphics $branchPen 662 512 974 512
Draw-Line $graphics $branchPen 150 150 405 405
Draw-Line $graphics $branchPen 619 405 874 150
Draw-Line $graphics $branchPen 619 619 874 874
Draw-Line $graphics $branchPen 150 874 405 619
foreach ($node in @(@(512,50), @(512,974), @(50,512), @(974,512), @(150,150), @(874,150), @(874,874), @(150,874))) {
    $graphics.FillEllipse($nodeBrush, $node[0] - 16, $node[1] - 16, 32, 32)
}

$junctionFile = Join-Path $outputDir "IRIS_NeuralBrush_Junction.png"
$bitmap.Save($junctionFile, [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()

$mainPen.Dispose()
$branchPen.Dispose()
$nodeBrush.Dispose()

Write-Output $pathFile
Write-Output $junctionFile
