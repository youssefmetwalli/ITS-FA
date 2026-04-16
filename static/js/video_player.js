(function () {
    const config = window.videoLearningConfig || {};
    const checkpointQuestions = Array.isArray(config.checkpointQuestions) ? config.checkpointQuestions : [];
    const progress = config.progress || {};
    const answeredCheckpointIds = new Set(progress.answered_checkpoint_ids || []);

    const chatMessages = document.getElementById("video-chat-messages");
    const chatForm = document.getElementById("video-chat-form");
    const chatInput = document.getElementById("video-chat-input");
    const summaryContent = document.getElementById("video-summary-content");
    const transcriptStatusText = document.getElementById("transcript-status-text");
    const transcriptStatusPill = document.getElementById("transcript-status-pill");
    const refreshTranscriptButton = document.getElementById("refresh-transcript-button");

    const checkpointModal = document.getElementById("checkpoint-modal");
    const checkpointQuestionText = document.getElementById("checkpoint-question-text");
    const checkpointOptions = document.getElementById("checkpoint-options");
    const checkpointFeedback = document.getElementById("checkpoint-feedback");
    const checkpointSubmitButton = document.getElementById("checkpoint-submit-button");
    const checkpointResumeButton = document.getElementById("checkpoint-resume-button");

    let player = null;
    let progressInterval = null;
    let activeCheckpoint = null;
    let lastProgressSyncSecond = 0;
    let summaryRequested = Boolean(progress.summary_shown || (summaryContent && summaryContent.querySelector(".summary-grid")));

    function escapeHtml(value) {
        return String(value || "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    function appendChatMessage(role, text, sourceType) {
        if (!chatMessages) {
            return;
        }
        const message = document.createElement("div");
        message.className = `chat-message ${role}`;
        message.innerHTML = `${escapeHtml(text)}${sourceType ? `<span class="chat-source">Source: ${escapeHtml(sourceType)}</span>` : ""}`;
        chatMessages.appendChild(message);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function setTranscriptStatus(message, available) {
        if (transcriptStatusText) {
            transcriptStatusText.textContent = message;
        }
        if (transcriptStatusPill) {
            transcriptStatusPill.textContent = available ? "Transcript ready" : "Transcript pending";
            transcriptStatusPill.title = message || "";
        }
    }

    function renderSummary(summary) {
        if (!summaryContent || !summary) {
            return;
        }
        const concepts = Array.isArray(summary.key_concepts)
            ? summary.key_concepts.map((item) => `<li>${escapeHtml(item)}</li>`).join("")
            : "";
        const takeaways = Array.isArray(summary.important_takeaways)
            ? summary.important_takeaways.map((item) => `<li>${escapeHtml(item)}</li>`).join("")
            : "";

        summaryContent.innerHTML = `
            <div class="summary-grid">
                <p>${escapeHtml(summary.narrative_summary || "")}</p>
                ${concepts ? `<div><strong>Key Concepts</strong><ul class="summary-list">${concepts}</ul></div>` : ""}
                ${takeaways ? `<div><strong>Important Takeaways</strong><ul class="summary-list">${takeaways}</ul></div>` : ""}
            </div>
        `;
    }

    function postProgress(extraPayload) {
        if (!config.progressEndpoint) {
            return Promise.resolve();
        }
        const watchedSeconds = player && typeof player.getCurrentTime === "function" ? player.getCurrentTime() : progress.watched_seconds || 0;
        const durationSeconds = player && typeof player.getDuration === "function" ? player.getDuration() : progress.duration_seconds || 0;
        return fetch(config.progressEndpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                watched_seconds: watchedSeconds,
                duration_seconds: durationSeconds,
                ...extraPayload,
            }),
        }).catch(() => null);
    }

    function requestSummary() {
        if (summaryRequested || !config.summaryEndpoint) {
            return;
        }
        summaryRequested = true;
        fetch(config.summaryEndpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ completed: true }),
        })
            .then((response) => response.json())
            .then((payload) => {
                if (payload.summary) {
                    renderSummary(payload.summary);
                    postProgress({ completed: true, summary_shown: true });
                } else if (summaryContent && payload.message) {
                    summaryContent.innerHTML = `<p class="summary-placeholder">${escapeHtml(payload.message)}</p>`;
                }
            })
            .catch(() => {
                if (summaryContent) {
                    summaryContent.innerHTML = "<p class=\"summary-placeholder\">Summary generation is temporarily unavailable.</p>";
                }
            });
    }

    function hideCheckpointModal() {
        if (!checkpointModal) {
            return;
        }
        checkpointModal.classList.remove("show");
        checkpointModal.setAttribute("aria-hidden", "true");
    }

    function showCheckpointModal(checkpoint) {
        if (!checkpointModal || !checkpointQuestionText || !checkpointOptions) {
            return;
        }
        activeCheckpoint = checkpoint;
        checkpointQuestionText.textContent = checkpoint.question || "Checkpoint question";
        checkpointOptions.innerHTML = "";
        checkpointFeedback.className = "checkpoint-feedback";
        checkpointFeedback.textContent = "";
        checkpointResumeButton.disabled = true;

        (checkpoint.options || []).forEach((option, index) => {
            const optionId = `checkpoint-option-${index}`;
            const wrapper = document.createElement("label");
            wrapper.className = "checkpoint-option";
            wrapper.innerHTML = `
                <input type="radio" name="checkpoint-option" id="${optionId}" value="${escapeHtml(option)}">
                <span>${escapeHtml(option)}</span>
            `;
            checkpointOptions.appendChild(wrapper);
        });

        checkpointModal.classList.add("show");
        checkpointModal.setAttribute("aria-hidden", "false");
    }

    function maybeTriggerCheckpoint() {
        if (!player || typeof player.getCurrentTime !== "function" || activeCheckpoint) {
            return;
        }
        const currentTime = player.getCurrentTime();
        const nextCheckpoint = checkpointQuestions.find((checkpoint) => {
            const checkpointId = checkpoint.checkpoint_id || checkpoint.timestamp_seconds;
            return !answeredCheckpointIds.has(String(checkpointId)) && currentTime >= Number(checkpoint.timestamp_seconds || 0);
        });
        if (!nextCheckpoint) {
            return;
        }
        if (typeof player.pauseVideo === "function") {
            player.pauseVideo();
        }
        showCheckpointModal(nextCheckpoint);
    }

    function setupCheckpointHandlers() {
        if (!checkpointSubmitButton || !checkpointResumeButton) {
            return;
        }

        checkpointSubmitButton.addEventListener("click", function () {
            if (!activeCheckpoint) {
                return;
            }
            const selected = document.querySelector('input[name="checkpoint-option"]:checked');
            if (!selected) {
                checkpointFeedback.className = "checkpoint-feedback show incorrect";
                checkpointFeedback.textContent = "Select one answer before continuing.";
                return;
            }

            const correctAnswer = activeCheckpoint.correct_answer || "";
            const isCorrect = selected.value === correctAnswer;
            checkpointFeedback.className = `checkpoint-feedback show ${isCorrect ? "correct" : "incorrect"}`;
            checkpointFeedback.textContent = isCorrect
                ? `Correct. ${activeCheckpoint.explanation || ""}`.trim()
                : `Not quite. ${activeCheckpoint.explanation || `Correct answer: ${correctAnswer}`}`.trim();
            checkpointResumeButton.disabled = false;
        });

        checkpointResumeButton.addEventListener("click", function () {
            if (!activeCheckpoint) {
                return;
            }
            const checkpointId = String(activeCheckpoint.checkpoint_id || activeCheckpoint.timestamp_seconds);
            answeredCheckpointIds.add(checkpointId);
            postProgress({ answered_checkpoint_id: checkpointId });
            hideCheckpointModal();
            const resumePlayer = player;
            activeCheckpoint = null;
            if (resumePlayer && typeof resumePlayer.playVideo === "function") {
                resumePlayer.playVideo();
            }
        });
    }

    function maybeSyncProgress() {
        if (!player || typeof player.getCurrentTime !== "function" || typeof player.getDuration !== "function") {
            return;
        }
        const currentTime = Math.floor(player.getCurrentTime());
        const duration = player.getDuration();
        if (currentTime - lastProgressSyncSecond >= 8) {
            lastProgressSyncSecond = currentTime;
            postProgress({});
        }
        if (duration > 0 && (currentTime / duration) >= 0.85) {
            requestSummary();
        }
    }

    function startProgressMonitor() {
        if (progressInterval) {
            return;
        }
        progressInterval = window.setInterval(function () {
            maybeTriggerCheckpoint();
            maybeSyncProgress();
        }, 800);
    }

    function setupVideoChat() {
        if (!chatForm || !chatInput || !config.askEndpoint) {
            return;
        }

        chatForm.addEventListener("submit", function (event) {
            event.preventDefault();
            const message = chatInput.value.trim();
            if (!message) {
                return;
            }

            appendChatMessage("user", message);
            chatInput.value = "";

            fetch(config.askEndpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message }),
            })
                .then((response) => response.json())
                .then((payload) => {
                    appendChatMessage(
                        "assistant",
                        payload.response_text || "I could not answer that question.",
                        payload.source_type || "video"
                    );
                })
                .catch(() => {
                    appendChatMessage("assistant", "Video Q&A is temporarily unavailable.", "video");
                });
        });
    }

    function setupTranscriptRefresh() {
        if (!refreshTranscriptButton || !config.transcriptRefreshEndpoint) {
            return;
        }

        refreshTranscriptButton.addEventListener("click", function () {
            refreshTranscriptButton.disabled = true;
            refreshTranscriptButton.textContent = "Refreshing...";
            fetch(config.transcriptRefreshEndpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
            })
                .then((response) => response.json())
                .then((payload) => {
                    setTranscriptStatus(payload.message || "Transcript status updated.", Boolean(payload.transcript_available));
                })
                .catch(() => {
                    setTranscriptStatus("Transcript refresh failed.", false);
                })
                .finally(() => {
                    refreshTranscriptButton.disabled = false;
                    refreshTranscriptButton.textContent = "Refresh Transcript";
                });
        });
    }

    window.onYouTubeIframeAPIReady = function () {
        if (!config.youtubeVideoId || !window.YT || !document.getElementById("video-player")) {
            return;
        }
        player = new window.YT.Player("video-player", {
            videoId: config.youtubeVideoId,
            playerVars: { rel: 0, modestbranding: 1 },
            events: {
                onReady: function () {
                    startProgressMonitor();
                },
                onStateChange: function (event) {
                    if (event.data === window.YT.PlayerState.ENDED) {
                        postProgress({ completed: true });
                        requestSummary();
                    }
                },
            },
        });
    };

    setupCheckpointHandlers();
    setupVideoChat();
    setupTranscriptRefresh();
})();
