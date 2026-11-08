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

function Draw-Line {
    param($Graphics, [float]$Width, [System.Drawing.PointF[]]$Points)
    $pen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, $Width)
    $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $Graphics.DrawLines($pen, $Points)
    $pen.Dispose()
}

function Draw-Node {
    param($Graphics, [float]$X, [float]$Y, [float]$Radius)
    $brush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
    $Graphics.FillEllipse($brush, $X - $Radius, $Y - $Radius, $Radius * 2, $Radius * 2)
    $brush.Dispose()
}

$pathCanvas = New-Canvas
$pathBitmap = $pathCanvas[0]
$pathGraphics = $pathCanvas[1]

Draw-Line $pathGraphics 24 @(
    [System.Drawing.PointF]::new(512, 0),
    [System.Drawing.PointF]::new(512, 205),
    [System.Drawing.PointF]::new(402, 330),
    [System.Drawing.PointF]::new(512, 470),
    [System.Drawing.PointF]::new(512, 1024)
)
Draw-Line $pathGraphics 18 @(
    [System.Drawing.PointF]::new(402, 330),
    [System.Drawing.PointF]::new(250, 410),
    [System.Drawing.PointF]::new(155, 560)
)
Draw-Line $pathGraphics 18 @(
    [System.Drawing.PointF]::new(512, 470),
    [System.Drawing.PointF]::new(660, 575),
    [System.Drawing.PointF]::new(780, 705),
    [System.Drawing.PointF]::new(870, 705)
)
Draw-Node $pathGraphics 512 205 25
Draw-Node $pathGraphics 402 330 22
Draw-Node $pathGraphics 512 470 25
Draw-Node $pathGraphics 250 410 18
Draw-Node $pathGraphics 780 705 18

$pathFile = Join-Path $outputDir "IRIS_NeuralBrush_Path.png"
$pathBitmap.Save($pathFile, [System.Drawing.Imaging.ImageFormat]::Png)
$pathGraphics.Dispose()
$pathBitmap.Dispose()

$junctionCanvas = New-Canvas
$junctionBitmap = $junctionCanvas[0]
$junctionGraphics = $junctionCanvas[1]

Draw-Line $junctionGraphics 22 @(
    [System.Drawing.PointF]::new(512, 0),
    [System.Drawing.PointF]::new(512, 268)
)
Draw-Line $junctionGraphics 22 @(
    [System.Drawing.PointF]::new(512, 756),
    [System.Drawing.PointF]::new(512, 1024)
)
Draw-Line $junctionGraphics 18 @(
    [System.Drawing.PointF]::new(268, 512),
    [System.Drawing.PointF]::new(0, 512)
)
Draw-Line $junctionGraphics 18 @(
    [System.Drawing.PointF]::new(756, 512),
    [System.Drawing.PointF]::new(1024, 512)
)
Draw-Line $junctionGraphics 28 @(
    [System.Drawing.PointF]::new(512, 250),
    [System.Drawing.PointF]::new(774, 512),
    [System.Drawing.PointF]::new(512, 774),
    [System.Drawing.PointF]::new(250, 512),
    [System.Drawing.PointF]::new(512, 250)
)
Draw-Line $junctionGraphics 16 @(
    [System.Drawing.PointF]::new(512, 342),
    [System.Drawing.PointF]::new(682, 512),
    [System.Drawing.PointF]::new(512, 682),
    [System.Drawing.PointF]::new(342, 512),
    [System.Drawing.PointF]::new(512, 342)
)
Draw-Node $junctionGraphics 512 512 36
Draw-Node $junctionGraphics 512 250 20
Draw-Node $junctionGraphics 774 512 20
Draw-Node $junctionGraphics 512 774 20
Draw-Node $junctionGraphics 250 512 20

$junctionFile = Join-Path $outputDir "IRIS_NeuralBrush_Junction.png"
$junctionBitmap.Save($junctionFile, [System.Drawing.Imaging.ImageFormat]::Png)
$junctionGraphics.Dispose()
$junctionBitmap.Dispose()
