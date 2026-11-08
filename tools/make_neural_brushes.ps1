Add-Type -AssemblyName System.Drawing

$out = Join-Path $PSScriptRoot "..\assets\textures\iris_stockings"
New-Item -ItemType Directory -Force -Path $out | Out-Null

function New-BrushCanvas {
    $bmp = [Drawing.Bitmap]::new(1024, 1024)
    $gfx = [Drawing.Graphics]::FromImage($bmp)
    $gfx.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $gfx.Clear([Drawing.Color]::Black)
    return @($bmp, $gfx)
}

function Draw-Path($gfx, $points, $width) {
    $pen = [Drawing.Pen]::new([Drawing.Color]::White, $width)
    $pen.StartCap = $pen.EndCap = [Drawing.Drawing2D.LineCap]::Round
    $pen.LineJoin = [Drawing.Drawing2D.LineJoin]::Round
    $gfx.DrawLines($pen, $points)
    $pen.Dispose()
}

function Draw-Node($gfx, $x, $y, $radius) {
    $brush = [Drawing.SolidBrush]::new([Drawing.Color]::White)
    $gfx.FillEllipse($brush, $x - $radius, $y - $radius, $radius * 2, $radius * 2)
    $brush.Dispose()
}

$canvas = New-BrushCanvas
$bmp = $canvas[0]
$gfx = $canvas[1]
Draw-Path $gfx @(
    [Drawing.Point]::new(510, 28), [Drawing.Point]::new(510, 170),
    [Drawing.Point]::new(420, 265), [Drawing.Point]::new(420, 430),
    [Drawing.Point]::new(560, 550), [Drawing.Point]::new(560, 730),
    [Drawing.Point]::new(510, 835), [Drawing.Point]::new(510, 996)
) 22
Draw-Path $gfx @([Drawing.Point]::new(420,265),[Drawing.Point]::new(245,205),[Drawing.Point]::new(125,285)) 16
Draw-Path $gfx @([Drawing.Point]::new(560,550),[Drawing.Point]::new(750,475),[Drawing.Point]::new(900,560)) 16
Draw-Path $gfx @([Drawing.Point]::new(510,835),[Drawing.Point]::new(325,800),[Drawing.Point]::new(205,875)) 14
foreach ($p in @(@(510,170),@(420,265),@(420,430),@(560,550),@(560,730),@(510,835),@(245,205),@(750,475))) {
    Draw-Node $gfx $p[0] $p[1] 22
}
$gfx.Dispose()
$bmp.Save((Join-Path $out "IRIS_NeuralBrush_Path.png"), [Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()

$canvas = New-BrushCanvas
$bmp = $canvas[0]
$gfx = $canvas[1]
Draw-Path $gfx @([Drawing.Point]::new(512,55),[Drawing.Point]::new(512,970)) 20
Draw-Path $gfx @([Drawing.Point]::new(512,512),[Drawing.Point]::new(270,310),[Drawing.Point]::new(105,390)) 17
Draw-Path $gfx @([Drawing.Point]::new(512,512),[Drawing.Point]::new(755,310),[Drawing.Point]::new(920,390)) 17
Draw-Path $gfx @([Drawing.Point]::new(512,512),[Drawing.Point]::new(270,715),[Drawing.Point]::new(105,635)) 17
Draw-Path $gfx @([Drawing.Point]::new(512,512),[Drawing.Point]::new(755,715),[Drawing.Point]::new(920,635)) 17
$diamond = @(
    [Drawing.Point]::new(512,330), [Drawing.Point]::new(690,512),
    [Drawing.Point]::new(512,694), [Drawing.Point]::new(334,512),
    [Drawing.Point]::new(512,330)
)
Draw-Path $gfx $diamond 24
Draw-Node $gfx 512 512 34
foreach ($p in @(@(512,330),@(690,512),@(512,694),@(334,512),@(270,310),@(755,310),@(270,715),@(755,715))) {
    Draw-Node $gfx $p[0] $p[1] 19
}
$gfx.Dispose()
$bmp.Save((Join-Path $out "IRIS_NeuralBrush_Junction.png"), [Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
