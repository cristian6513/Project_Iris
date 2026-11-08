/** Real offline Vision QA. The caller owns the isolated page/server/network log. */
export async function verifyLocalVision(page, origin, { companion = false } = {}) {
    const base = new URL(origin);
    if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password
        || base.pathname !== '/' || base.search || base.hash) {
        throw new Error('Local Vision QA requires an HTTP origin');
    }
    if (new URL(page.url()).origin !== base.origin) {
        throw new Error('Local Vision QA page must already be on the tested origin');
    }
    return page.evaluate(async ({ origin, timeoutMs, companion }) => {
        let canvas, video, stream, mocap, drawFrame = 0, deadline, loadCachedModel;
        let drawTicks = 0, videoFrameTicks = 0, videoFrameRequest = 0, lastVideoFrame;
        let app, savedApp, savedPrivacy;
        const fail = message => { throw new Error(message); };
        const waitForObservation = async () => {
            const initialVideoTime = video.currentTime;
            const initialDrawTicks = drawTicks;
            const initialVideoFrameTicks = videoFrameTicks;
            // Software-rendered VRM rooms can share a slow GPU with the real
            // model in headless QA. This proves resources and inference, not
            // a hardware performance threshold.
            const until = performance.now() + 30000;
            while (performance.now() < until) {
                const observation = mocap.latestPresence();
                if (observation && typeof observation.present === 'boolean'
                    && Number.isFinite(observation.timestamp)) return { ...observation };
                await new Promise(resolve => setTimeout(resolve, 25));
            }
            const diagnostics = {
                mocap: { running: mocap.running, stats: { ...mocap.stats },
                    diagnostics: mocap.diagnostics(), presence: mocap.presenceObservation,
                    last_video_timestamp: mocap._lastTs,
                    tuning: { enabled: mocap.tuning.enabled, state: mocap.tuning.state,
                        interval: mocap.tuning.interval, pause_until: mocap.tuning.pauseUntil,
                        last_frame: mocap.tuning.lastFrame, calibration: mocap.tuning.calibration } },
                video: { current_time: video.currentTime, initial_time: initialVideoTime,
                    ready_state: video.readyState, paused: video.paused, ended: video.ended,
                    width: video.videoWidth, height: video.videoHeight,
                    error: video.error ? { code: video.error.code, message: video.error.message } : null,
                    tracks: stream.getVideoTracks().map(track => ({ ready_state: track.readyState,
                        muted: track.muted, enabled: track.enabled, settings: track.getSettings() })) },
                document: { visibility: document.visibilityState, has_focus: document.hasFocus() },
                frames: { draw_ticks: drawTicks, draw_ticks_while_waiting: drawTicks - initialDrawTicks,
                    video_ticks: videoFrameTicks, video_ticks_while_waiting: videoFrameTicks - initialVideoFrameTicks,
                    last_video_frame: lastVideoFrame },
            };
            // Invoke the actual cached raw model once to expose an error that
            // the production sampling loop intentionally catches. No loader,
            // delegate, model, timestamp method or inference is replaced.
            if (loadCachedModel) {
                const started = performance.now();
                try {
                    const result = (await loadCachedModel()).detectForVideo(video, performance.now());
                    diagnostics.manual_detection = { completed: true, elapsed_ms: performance.now() - started,
                        face_landmarks: result.faceLandmarks?.length ?? 0,
                        face_blendshapes: result.faceBlendshapes?.length ?? 0,
                        face_matrices: result.facialTransformationMatrixes?.length ?? 0 };
                } catch (error) {
                    diagnostics.manual_detection = { completed: false, elapsed_ms: performance.now() - started,
                        name: error.name, message: error.message, stack: error.stack };
                }
            }
            console.error('[local-vision-qa] inference timeout diagnostics', JSON.stringify(diagnostics));
            fail('Real Face Landmarker did not complete inference on synthetic video: ' + JSON.stringify(diagnostics));
        };
        const probe = async () => {
            canvas = document.createElement('canvas');
            canvas.width = 320; canvas.height = 240;
            const context = canvas.getContext('2d');
            if (!context || typeof canvas.captureStream !== 'function') fail('Synthetic video capture is unavailable');
            video = document.createElement('video');
            video.muted = true; video.autoplay = true; video.playsInline = true;
            video.style.cssText = 'position:fixed;left:0;top:0;width:2px;height:2px;opacity:0.01;pointer-events:none';
            document.body.append(video);
            // Flat background and a moving rectangle contain no face. Every
            // completed no-face inference is valid evidence, not a model stub.
            const draw = () => {
                drawTicks += 1;
                context.fillStyle = '#707070'; context.fillRect(0, 0, canvas.width, canvas.height);
                context.fillStyle = '#303030';
                context.fillRect(Math.floor(performance.now() / 20) % 280, 100, 24, 24);
                drawFrame = requestAnimationFrame(draw);
            };
            draw();
            stream = canvas.captureStream(24);
            video.srcObject = stream;
            await video.play();
            if (typeof video.requestVideoFrameCallback === 'function') {
                const frame = (_now, metadata) => {
                    videoFrameTicks += 1;
                    lastVideoFrame = { media_time: metadata.mediaTime, presented_frames: metadata.presentedFrames,
                        width: metadata.width, height: metadata.height };
                    videoFrameRequest = video.requestVideoFrameCallback(frame);
                };
                videoFrameRequest = video.requestVideoFrameCallback(frame);
            }
            if (!stream.getVideoTracks().length || video.videoWidth !== 320) fail('Synthetic video did not start');
            let start, stop;
            if (companion) {
                app = window.irisCompanion;
                if (!app || typeof app._startLocalPerception !== 'function'
                    || typeof app._stopLocalPerception !== 'function') fail('Compiled Companion is not initialized');
                if (app.cameraStream || app.mocap) fail('Companion Vision QA requires a fresh camera-free page');
                savedApp = Object.fromEntries([
                    'cameraStream', 'cameraPreview', 'mocap', 'emotionClassifier',
                    '_cameraAnalysisGeneration', '_lastMocapTimestamp',
                    '_emotionEmitAccum', '_visionEmitAccum',
                ].map(key => [key, app[key]]));
                savedPrivacy = {
                    analysisState: app.cameraPrivacy?.dataset.analysisState,
                    label: app.cameraPrivacyLabel?.textContent,
                };
                app.cameraStream = stream;
                app.cameraPreview = video;
                // Call the compiled app's real module loader and resource URLs.
                // Only its input MediaStream/video are synthetic; no runtime,
                // URL, model bytes, factory or inference method is replaced.
                start = async () => { await app._startLocalPerception(); mocap = app.mocap; };
                stop = () => app._stopLocalPerception();
            } else {
                const { CameraMocap, loadMediaPipe } = await import(
                    `${origin}/face/js/camera_mocap.js?local-vision-qa=1`
                );
                mocap = new CameraMocap();
                start = () => mocap.start(video);
                stop = () => mocap.stop();
                loadCachedModel = loadMediaPipe;
            }
            await start();
            if (!mocap?.running) fail('Local Vision did not start');
            const firstMocap = mocap, firstOptions = mocap.options;
            if (companion) {
                if (firstOptions.wasmBase !== `${origin}/face/vendor/mediapipe/wasm/`
                    || firstOptions.modelUrl !== `${origin}/face/vendor/mediapipe/face_landmarker.task`
                    || typeof firstOptions.moduleLoader !== 'function') fail('Compiled Companion did not select prepared local Vision resources');
            }
            const first = await waitForObservation();
            const firstStats = { ...mocap.stats };
            if (first.present !== false || firstStats.misses < 1) fail('Synthetic no-face inference result is invalid');
            const landmarker = loadCachedModel ? await loadCachedModel() : null;
            if (landmarker && typeof landmarker.detectForVideo !== 'function') fail('Real Face Landmarker was not loaded');
            stop();
            if (mocap.running || mocap.latest() !== null || mocap.latestPresence() !== null) fail('Vision stop did not clear observations');
            if (loadCachedModel && await loadCachedModel() !== landmarker) fail('Vision stop discarded the cached model');
            await start();
            if (mocap !== firstMocap || mocap.options !== firstOptions) fail('Vision restart replaced its real camera runtime instance');
            const restarted = await waitForObservation();
            if (restarted.timestamp <= first.timestamp || restarted.present !== false) fail('Cached model did not resume inference');
            if (loadCachedModel && await loadCachedModel() !== landmarker) fail('Vision restart replaced the cached model');
            return { ready: true, surface: companion ? 'compiled-companion' : 'face', synthetic_video: true,
                ...(companion ? {} : { model_cached: true }), instance_cached: true,
                cache_evidence: companion ? 'same compiled mocap instance with new inference' : 'same landmarker and mocap instances with new inference',
                ...(companion ? { wasm_base: firstOptions.wasmBase, model_url: firstOptions.modelUrl } : {}),
                first, restarted, first_stats: firstStats, restart_stats: { ...mocap.stats },
                resources: '/face/vendor/mediapipe/' };
        };
        try {
            return await Promise.race([
                probe(),
                new Promise((_, reject) => { deadline = setTimeout(() => reject(new Error('Local Vision QA timed out')), timeoutMs); }),
            ]);
        } finally {
            clearTimeout(deadline);
            if (app && savedApp) app._stopLocalPerception();
            mocap?.dispose();
            if (drawFrame) cancelAnimationFrame(drawFrame);
            if (videoFrameRequest) video.cancelVideoFrameCallback(videoFrameRequest);
            stream?.getTracks().forEach(track => track.stop());
            if (video) { video.pause(); video.srcObject = null; video.remove(); }
            canvas?.remove();
            if (app && savedApp) {
                Object.assign(app, savedApp);
                if (app.cameraPrivacy) {
                    if (savedPrivacy.analysisState === undefined) delete app.cameraPrivacy.dataset.analysisState;
                    else app.cameraPrivacy.dataset.analysisState = savedPrivacy.analysisState;
                }
                if (app.cameraPrivacyLabel) app.cameraPrivacyLabel.textContent = savedPrivacy.label;
            }
        }
    }, { origin: base.origin, timeoutMs: 90000, companion });
}
