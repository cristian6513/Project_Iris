Add-Type -AssemblyName System.Drawing

$outputDir = Join-Path (Split-Path $PSScriptRoot -Parent) "assets\textures\iris_stockings"
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

function New-Point([float]$x, [float]$y) {
    New-Object System.Drawing.PointF($x, $y)
}

function Draw-GlowLine($graphics, [System.Drawing.PointF[]]$points, [float]$width = 13) {
    foreach ($layer in @(
        @{ Width = $width * 3.2; Color = [System.Drawing.Color]::FromArgb(30, 255, 255, 255) },
        @{ Width = $width * 2.0; Color = [System.Drawing.Color]::FromArgb(65, 255, 255, 255) },
        @{ Width = $width; Color = [System.Drawing.Color]::White }
    )) {
        $pen = New-Object System.Drawing.Pen($layer.Color, $layer.Width)
        $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
        $graphics.DrawLines($pen, $points)
        $pen.Dispose()
    }
}

function Draw-Node($graphics, [float]$x, [float]$y, [float]$radius = 13) {
    $glow = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(60, 255, 255, 255))
    $core = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
    $graphics.FillEllipse($glow, $x - $radius * 2, $y - $radius * 2, $radius * 4, $radius * 4)
    $graphics.FillEllipse($core, $x - $radius, $y - $radius, $radius * 2, $radius * 2)
    $glow.Dispose()
    $core.Dispose()
}

function New-BrushImage($fileName, [scriptblock]$draw) {
    $bitmap = New-Object System.Drawing.Bitmap(1024, 1024, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.Color]::Black)
    & $draw $graphics
    $path = Join-Path $outputDir $fileName
    $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose()
    $bitmap.Dispose()
}

New-BrushImage "IRIS_NeuralBrush_Path.png" {
    param($g)
    Draw-GlowLine $g @(
        (New-Point 118 828), (New-Point 220 724), (New-Point 338 688),
        (New-Point 438 574), (New-Point 548 530), (New-Point 650 392),
        (New-Point 786 340), (New-Point 906 190)
    ) 16
    Draw-GlowLine $g @((New-Point 338 688), (New-Point 266 556), (New-Point 164 506)) 11
    Draw-GlowLine $g @((New-Point 548 530), (New-Point 470 410), (New-Point 506 286)) 11
    Draw-GlowLine $g @((New-Point 650 392), (New-Point 748 496), (New-Point 870 476)) 11
    Draw-GlowLine $g @((New-Point 786 340), (New-Point 728 230), (New-Point 758 122)) 9
    foreach ($node in @(
        @(118,828,13), @(338,688,15), @(548,530,16), @(650,392,14),
        @(786,340,15), @(906,190,13), @(164,506,11), @(506,286,11),
        @(870,476,11), @(758,122,10)
    )) {
        Draw-Node $g $node[0] $node[1] $node[2]
    }
}

New-BrushImage "IRIS_NeuralBrush_Junction.png" {
    param($g)
    Draw-GlowLine $g @(
        (New-Point 512 104), (New-Point 790 350), (New-Point 512 662),
        (New-Point 234 350), (New-Point 512 104)
    ) 15
    Draw-GlowLine $g @(
        (New-Point 512 224), (New-Point 670 362), (New-Point 512 536),
        (New-Point 354 362), (New-Point 512 224)
    ) 10
    Draw-GlowLine $g @((New-Point 512 104), (New-Point 512 40)) 11
    Draw-GlowLine $g @((New-Point 234 350), (New-Point 104 350), (New-Point 52 286)) 11
    Draw-GlowLine $g @((New-Point 790 350), (New-Point 920 350), (New-Point 972 286)) 11
    Draw-GlowLine $g @((New-Point 512 662), (New-Point 512 828), (New-Point 392 948)) 11
    Draw-GlowLine $g @((New-Point 512 828), (New-Point 632 948)) 11
    foreach ($node in @(
        @(512,104,14), @(790,350,14), @(512,662,17), @(234,350,14),
        @(512,362,20), @(104,350,11), @(920,350,11), @(512,828,13),
        @(392,948,10), @(632,948,10)
    )) {
        Draw-Node $g $node[0] $node[1] $node[2]
    }
}
