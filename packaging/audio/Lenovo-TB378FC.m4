# SPDX-License-Identifier: BSD-3-Clause
# AudioReach topology for Lenovo Xiaoxin Pad Pro GT 13 (TB378FC):
#  - Playback: MultiMedia1 (S16_LE, 48 kHz, 4ch FL/FR/RL/RR) -> Secondary TDM0
#              (4x AW88480 amplifiers on TDM slots 0..3: BL, TL, TR, BR)
#  - Capture:  TX_CODEC_DMA_TX_3 (S16_LE, 48 kHz, 2ch WCD9390 AMIC1+AMIC4)
#              -> MultiMedia3 (PCM2c)
include(`audioreach/audioreach.m4')
include(`audioreach/stream-subgraph.m4')
include(`audioreach/device-subgraph.m4')
include(`util/route.m4')
include(`util/mixer.m4')
include(`audioreach/tokens.m4')
include(`audioreach/module_tdm.m4')
include(`audioreach/module_mfc_cfg.m4')

# Quad-channel playback stream so PipeWire's orientation filter-chain can route
# screen-relative Left/Right across all four physical AW88480 speakers.
STREAM_SG_PCM_ADD(audioreach/subgraph-stream-vol-playback.m4, FRONTEND_DAI_MULTIMEDIA1,
	`S16_LE', 48000, 48000, 2, 4,
	0x00004001, 0x00004001, 0x00006001, `110000')

# Dual-microphone capture stream + TX_CODEC_DMA_TX_3 device subgraph (WCD9390 ADC1+ADC3)
STREAM_SG_PCM_ADD(audioreach/subgraph-stream-capture.m4, FRONTEND_DAI_MULTIMEDIA3,
	`S16_LE', 48000, 48000, 1, 2,
	0x00004003, 0x00004003, 0x00006200, `110000')

DEVICE_SG_ADD(audioreach/subgraph-device-codec-dma-capture.m4, `TX_CODEC_DMA_TX_3', TX_CODEC_DMA_TX_3,
	`S16_LE', 48000, 48000, 2, 2,
	LPAIF_INTF_TYPE_RXTX, CODEC_INTF_IDX_TX3, 0, DATA_FORMAT_FIXED_POINT,
	0x00004020, 0x00004020, 0x00006300)

STREAM_DEVICE_CAPTURE_MIXER(FRONTEND_DAI_MULTIMEDIA3, ``TX_CODEC_DMA_TX_3'')
STREAM_DEVICE_CAPTURE_ROUTE(FRONTEND_DAI_MULTIMEDIA3, ``MultiMedia3 Mixer'', ``TX_CODEC_DMA_TX_3, device120.logger1'')

# Secondary legacy TDM playback: LOG -> MFC -> 4-slot 32-bit TDM endpoint on lane SD1
define(`NAME_PREFIX', `device40') dnl
AR_SUBGRAPH(1, SECONDARY_TDM_RX_0, 0x00004010,
	APM_SG_PERF_MODE_LOW_LATENCY, APM_SUB_GRAPH_DIRECTION_TX, APM_SUB_GRAPH_SID_AUDIO_PLAYBACK)
AR_CONTAINER(1, 0x00004010, APM_CONTAINER_TYPE_ID_GC, 8192,
	APM_CONT_GRAPH_POS_STREAM, APM_PROC_DOMAIN_ID_ADSP)
AR_MODULE_LOG(1, 1, 1, 0x00006100, 1, 1, 1, 2, 0x000019ab, 1, 0, 0x00006101)
AR_MODULE_MFC_CFG(1, 1, 1, 0x00006101, 1, 1, 1, 2, 0x00006102)
AR_MODULE_TDM_RX(1, 1, 1, 0x00006102, LPAIF_INTF_TYPE_LPAIF, I2S_INTF_TYPE_SECONDARY,
	DATA_FORMAT_FIXED_POINT, 0x0f, 4, 32, 2, 1, 1, 0, 0, 0, `Secondary TDM0')

# Four-channel Q14 channel-mixer matrix (PARAM_ID_MFC_CHMIXER_CFG = 0x0800101f):
# Input map:  FL FR LS RS = 1 2 4 5 (top-left, top-right, bottom-left, bottom-right)
# Output map: FL FR LS RS = 1 2 4 5 addressing physical TB378FC TDM slots:
#   slot0 (Amp0 @ 0x35) = bottom-left  <- LS (input 2)
#   slot1 (Amp1 @ 0x34) = top-left     <- FL (input 0)
#   slot2 (Amp2 @ 0x36) = top-right    <- FR (input 1)
#   slot3 (Amp3 @ 0x37) = bottom-right <- RS (input 3)
# Header 32 B + payload 56 B (0x38) = 88 B (0x58).
SectionData."device40.mfc1_cfg" {
	words "0x00000058, 0x01001006, 0x00000000, 0x00000000,
		0x00006101, 0x0800101f, 0x00000038, 0x00000000,
		0x00000001, 0x00040004,
		0x00020001, 0x00050004,
		0x00020001, 0x00050004,
		0x00000000, 0x00004000,
		0x00004000, 0x00000000,
		0x40000000, 0x00000000,
		0x00000000, 0x40000000"
}

SectionGraph."Secondary TDM0 Hardware Graph" {
	index "1"
	lines [
		"device40.logger1, , Secondary TDM0 Audio Mixer"
		"device40.mfc1, , device40.logger1"
		"device40.tdm_rx1, , device40.mfc1"
		"Secondary TDM0 Playback, , device40.tdm_rx1"
	]
}
STREAM_DEVICE_PLAYBACK_MIXER(SECONDARY_TDM_RX_0, ``Secondary TDM0'', ``MultiMedia1'')
STREAM_DEVICE_PLAYBACK_ROUTE(SECONDARY_TDM_RX_0, ``Secondary TDM0 Audio Mixer'', ``MultiMedia1, stream0.logger1'')
