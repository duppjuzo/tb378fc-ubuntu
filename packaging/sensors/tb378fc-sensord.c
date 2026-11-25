// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * TB378FC SensorProxy & Magnetic Folio Lid daemon backed by sensh/SNS Client.
 *
 * Adapted from PickaxeHit/linux-zhiyu sensors/sensord.c for Ubuntu 26.04 GNOME:
 *  - Publishes net.hadess.SensorProxy (4-quadrant accelerometer + ambient light)
 *    with Android-style low-pass filtering, flat-table lock, hysteresis, and
 *    dynamic PropertiesChanged notifications so GNOME gsd-power immediately
 *    exposes the Automatic Screen Brightness toggle.
 *  - Decodes Qualcomm SSC sns_hall_event (msg 770) and emits SW_LID on a
 *    persistent /dev/uinput switch ("TB378FC Folio Lid") so systemd-logind
 *    suspends on magnetic cover close and wakes on open.
 */
#include <errno.h>
#include <fcntl.h>
#include <gio/gio.h>
#include <glib.h>
#include <linux/uinput.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/wait.h>
#include <unistd.h>

#define BUS_NAME         "net.hadess.SensorProxy"
#define OBJ_PATH         "/net/hadess/SensorProxy"
#define IFACE            "net.hadess.SensorProxy"
#define COMPASS_OBJ_PATH "/net/hadess/SensorProxy/Compass"
#define COMPASS_IFACE    "net.hadess.SensorProxy.Compass"
#ifndef SENSH
#define SENSH    "/usr/bin/sensh"
#endif

static const gchar introspection_xml[] =
	"<node>"
	"  <interface name='net.hadess.SensorProxy'>"
	"    <method name='ClaimAccelerometer'/>"
	"    <method name='ReleaseAccelerometer'/>"
	"    <method name='ClaimLight'/>"
	"    <method name='ReleaseLight'/>"
	"    <method name='ClaimProximity'/>"
	"    <method name='ReleaseProximity'/>"
	"    <property name='HasAccelerometer' type='b' access='read'/>"
	"    <property name='AccelerometerOrientation' type='s' access='read'/>"
	"    <property name='AccelerometerTilt' type='s' access='read'/>"
	"    <property name='HasAmbientLight' type='b' access='read'/>"
	"    <property name='LightLevelUnit' type='s' access='read'/>"
	"    <property name='LightLevel' type='d' access='read'/>"
	"    <property name='HasProximity' type='b' access='read'/>"
	"    <property name='ProximityNear' type='b' access='read'/>"
	"  </interface>"
	"</node>";

static const gchar compass_introspection_xml[] =
	"<node>"
	"  <interface name='net.hadess.SensorProxy.Compass'>"
	"    <method name='ClaimCompass'/>"
	"    <method name='ReleaseCompass'/>"
	"    <property name='HasCompass' type='b' access='read'/>"
	"    <property name='CompassHeading' type='d' access='read'/>"
	"  </interface>"
	"</node>";

typedef enum {
	STREAM_LIGHT,
	STREAM_ACCEL,
	STREAM_HALL,
	STREAM_COUNT,
} StreamType;

typedef struct Sensord Sensord;

typedef struct {
	Sensord *self;
	StreamType type;
	GSubprocess *proc;
	GDataInputStream *out;
} Stream;

struct Sensord {
	GDBusConnection *conn;
	guint owner_id;
	guint reg_id;
	guint compass_reg_id;

	gboolean has_light;
	gboolean light_valid;
	gboolean light_claimed;
	gdouble light;
	gboolean has_accel;
	gboolean accel_claimed;
	gchar *orientation;

	/* accelerometer filter / Android-style orientation state */
	gboolean filter_init;
	gdouble fx, fy, fz;
	gint64 last_sample_us;
	gboolean flat;
	gint64 flat_since_us;
	gint64 flat_end_us;
	gint64 accel_until_us;
	gint current_quadrant;
	gint candidate_quadrant;
	gint64 candidate_since_us;

	gboolean ready_notified;
	int lid_fd;
	int lid_state;
	Stream *streams[STREAM_COUNT];
};

static gboolean notify_ready(gpointer user_data)
{
	Sensord *self = user_data;
	GError *error = NULL;
	gint status;

	if (self->ready_notified)
		return G_SOURCE_REMOVE;
	if (!g_getenv("NOTIFY_SOCKET")) {
		self->ready_notified = TRUE;
		return G_SOURCE_REMOVE;
	}
	if (!g_spawn_command_line_sync("/usr/bin/systemd-notify --ready", NULL,
				      NULL, &status, &error)) {
		g_warning("notify readiness: %s", error->message);
		g_clear_error(&error);
		return G_SOURCE_CONTINUE;
	}
	if (!g_spawn_check_wait_status(status, &error)) {
		g_warning("notify readiness: %s", error->message);
		g_clear_error(&error);
		return G_SOURCE_CONTINUE;
	}
	self->ready_notified = TRUE;
	return G_SOURCE_REMOVE;
}

static void set_orientation(Sensord *self, const gchar *orientation);

static void emit_property(Sensord *self, const gchar *name, GVariant *value)
{
	GVariantBuilder changed;
	GVariant *invalid;

	if (!self->conn)
		return;
	g_variant_builder_init(&changed, G_VARIANT_TYPE("a{sv}"));
	g_variant_builder_add(&changed, "{sv}", name, value);
	invalid = g_variant_new_strv(NULL, 0);
	g_dbus_connection_emit_signal(self->conn, NULL, OBJ_PATH,
				      "org.freedesktop.DBus.Properties",
				      "PropertiesChanged",
				      g_variant_new("(sa{sv}@as)", IFACE,
						    &changed, invalid),
				      NULL);
}

static void set_has_light(Sensord *self, gboolean val)
{
	if (self->has_light == val)
		return;
	self->has_light = val;
	emit_property(self, "HasAmbientLight", g_variant_new_boolean(val));
}

static void set_has_accel(Sensord *self, gboolean val)
{
	if (self->has_accel == val)
		return;
	self->has_accel = val;
	emit_property(self, "HasAccelerometer", g_variant_new_boolean(val));
}

static const gchar *const quadrant_names[4] = {
	"left-up",	/*   0 deg: +y portrait */
	"bottom-up",	/*  90 deg: -x landscape 180 */
	"right-up",	/* 180 deg: -y portrait 180 */
	"normal",	/* 270 deg: +x landscape (TB378FC native) */
};

static void accel_process(Sensord *self, gdouble x, gdouble y, gdouble z)
{
	const gdouble g = 9.80665;
	const gdouble rad_to_deg = 57.29577951308232;
	gint64 now = g_get_monotonic_time();
	gdouble dt_ms;
	gdouble alpha;
	gdouble magnitude;
	gdouble tilt;
	gdouble angle;
	gint quadrant;
	gdouble center;
	gdouble diff;

	if (!self->filter_init || now - self->last_sample_us > 1000000) {
		self->fx = x;
		self->fy = y;
		self->fz = z;
		self->filter_init = TRUE;
	} else {
		dt_ms = (now - self->last_sample_us) / 1000.0;
		alpha = dt_ms / (200.0 + dt_ms);
		self->fx += alpha * (x - self->fx);
		self->fy += alpha * (y - self->fy);
		self->fz += alpha * (z - self->fz);
	}
	self->last_sample_us = now;

	magnitude = sqrt(self->fx * self->fx + self->fy * self->fy + self->fz * self->fz);
	if (magnitude < 0.1)
		return;

	if (fabs(magnitude - g) > 4.0)
		self->accel_until_us = now + 300000;
	if (now < self->accel_until_us)
		return;

	tilt = fabs(asin(CLAMP(self->fz / magnitude, -1.0, 1.0)) * rad_to_deg);
	if (tilt > 75.0) {
		if (!self->flat) {
			if (self->flat_since_us == 0)
				self->flat_since_us = now;
			else if (now - self->flat_since_us >= 1000000)
				self->flat = TRUE;
		}
		self->flat_end_us = 0;
	} else {
		self->flat_since_us = 0;
		if (self->flat) {
			if (self->flat_end_us == 0)
				self->flat_end_us = now;
			else if (now - self->flat_end_us >= 500000)
				self->flat = FALSE;
		}
	}
	if (self->flat)
		return;

	if (tilt > 65.0)
		return;

	angle = atan2(-self->fx, self->fy) * rad_to_deg;
	if (angle < 0.0)
		angle += 360.0;

	quadrant = ((gint)floor((angle + 45.0) / 90.0)) & 3;

	if (self->current_quadrant < 0) {
		self->current_quadrant = quadrant;
		set_orientation(self, quadrant_names[quadrant]);
		return;
	}

	if (quadrant == self->current_quadrant) {
		self->candidate_quadrant = -1;
		return;
	}

	center = self->current_quadrant * 90.0;
	diff = fabs(angle - center);
	if (diff > 180.0)
		diff = 360.0 - diff;
	if (diff < 60.0) {
		self->candidate_quadrant = -1;
		return;
	}

	if (quadrant != self->candidate_quadrant) {
		self->candidate_quadrant = quadrant;
		self->candidate_since_us = now;
	}
	if (now - self->candidate_since_us < 300000)
		return;

	self->current_quadrant = quadrant;
	self->candidate_quadrant = -1;
	set_orientation(self, quadrant_names[quadrant]);
}

static void set_spk_rotation(const gchar *orientation)
{
	static const struct {
		const gchar *orientation;
		const gchar *rotation;
	} map[] = {
		{ "normal", "0" },
		{ "bottom-up", "180" },
		{ "left-up", "90" },
		{ "right-up", "270" },
	};
	const gchar *rotation = NULL;
	const gchar *path = "/sys/module/q6apm_lpass_dais/parameters/spk_rotation";
	int fd;
	gsize i;

	for (i = 0; i < G_N_ELEMENTS(map); i++) {
		if (g_strcmp0(orientation, map[i].orientation) == 0) {
			rotation = map[i].rotation;
			break;
		}
	}
	if (!rotation)
		return;

	fd = open(path, O_WRONLY);
	if (fd < 0)
		return;
	if (write(fd, rotation, strlen(rotation)) < 0)
		g_warning("failed to write %s", path);
	close(fd);
}

static void set_orientation(Sensord *self, const gchar *orientation)
{
	if (g_strcmp0(self->orientation, orientation) == 0)
		return;
	g_free(self->orientation);
	self->orientation = g_strdup(orientation);
	if (self->accel_claimed)
		set_spk_rotation(self->orientation);
	emit_property(self, "AccelerometerOrientation",
		      g_variant_new_string(self->orientation));
}

static gboolean read_varint(const guint8 **p, const guint8 *end, guint64 *value)
{
	*value = 0;
	for (unsigned shift = 0; shift < 64 && *p < end; shift += 7) {
		guint8 b = *(*p)++;
		if (shift == 63 && b > 1)
			return FALSE;
		*value |= (guint64)(b & 0x7f) << shift;
		if (!(b & 0x80))
			return TRUE;
	}
	return FALSE;
}

static int decode_hall(const gchar *line)
{
	const gchar *hex = strstr(line, "received unknown event 770, raw: ");
	guint8 data[256];
	guint64 state = 2, quality = 0;
	gboolean have_state = FALSE, have_quality = FALSE;

	if (!hex)
		return -1;
	hex += strlen("received unknown event 770, raw: ");
	gsize len = strlen(hex);
	if (!len || len % 2 || len / 2 > sizeof(data))
		return -1;
	for (gsize i = 0; i < len / 2; i++) {
		int hi = g_ascii_xdigit_value(hex[2 * i]);
		int lo = g_ascii_xdigit_value(hex[2 * i + 1]);
		if (hi < 0 || lo < 0)
			return -1;
		data[i] = (hi << 4) | lo;
	}
	const guint8 *p = data, *end = data + len / 2;
	while (p < end) {
		guint64 tag, value;
		if (!read_varint(&p, end, &tag) || !(tag >> 3))
			return -1;
		if ((tag & 7) == 0) {
			if (!read_varint(&p, end, &value))
				return -1;
			if ((tag >> 3) == 1) {
				state = value;
				have_state = TRUE;
			} else if ((tag >> 3) == 2) {
				quality = value;
				have_quality = TRUE;
			}
		} else {
			guint64 skip;
			switch (tag & 7) {
			case 1: skip = 8; break;
			case 5: skip = 4; break;
			case 2:
				if (!read_varint(&p, end, &skip))
					return -1;
				break;
			default: return -1;
			}
			if (skip > (guint64)(end - p))
				return -1;
			p += skip;
		}
	}
	return have_state && have_quality && state <= 1 && quality >= 1 && quality <= 3
		? (int)state : -1;
}

static void update_lid(Sensord *self, int closed)
{
	if (self->lid_state == closed && self->lid_fd >= 0)
		return;
	if (self->lid_fd < 0) {
		struct uinput_setup setup = { .id.bustype = BUS_HOST };
		g_strlcpy(setup.name, "TB378FC Folio Lid", sizeof(setup.name));
		int fd = open("/dev/uinput", O_WRONLY | O_CLOEXEC);
		if (fd < 0 || ioctl(fd, UI_SET_EVBIT, EV_SW) < 0 ||
		    ioctl(fd, UI_SET_SWBIT, SW_LID) < 0 ||
		    ioctl(fd, UI_DEV_SETUP, &setup) < 0 ||
		    ioctl(fd, UI_DEV_CREATE) < 0) {
			g_warning("create folio input: %s", g_strerror(errno));
			if (fd >= 0)
				close(fd);
			self->lid_state = closed;
			return;
		}
		self->lid_fd = fd;
	}
	struct input_event events[2] = {
		{ .type = EV_SW, .code = SW_LID, .value = closed },
		{ .type = EV_SYN, .code = SYN_REPORT },
	};
	ssize_t n;
	do {
		n = write(self->lid_fd, events, sizeof(events));
	} while (n < 0 && errno == EINTR);
	if (n != sizeof(events)) {
		g_warning("write folio input: %s", n < 0 ? g_strerror(errno) : "short write");
		ioctl(self->lid_fd, UI_DEV_DESTROY);
		close(self->lid_fd);
		self->lid_fd = -1;
		self->lid_state = closed;
		return;
	}
	self->lid_state = closed;
	g_message("Folio lid %s (SW_LID=%d)", closed ? "closed" : "open", closed);
	notify_ready(self);
}

static void cleanup_stream(Stream *stream)
{
	Sensord *self = stream->self;
	if (self->streams[stream->type] == stream)
		self->streams[stream->type] = NULL;
	g_subprocess_force_exit(stream->proc);
	g_subprocess_wait_async(stream->proc, NULL, NULL, NULL);
	g_object_unref(stream->proc);
	g_object_unref(stream->out);
	g_free(stream);
}

static void read_stream_cb(GObject *source, GAsyncResult *res, gpointer user_data)
{
	Stream *stream = user_data;
	GDataInputStream *dis = G_DATA_INPUT_STREAM(source);
	GError *error = NULL;
	gsize len = 0;
	gchar *line;

	line = g_data_input_stream_read_line_finish(dis, res, &len, &error);
	if (!line) {
		if (error) {
			g_warning("sensh stream %d read error: %s", stream->type, error->message);
			g_error_free(error);
		} else {
			g_warning("sensh stream %d ended; scheduling reconnect", stream->type);
		}
		cleanup_stream(stream);
		return;
	}

	if (strstr(line, "sensor core left")) {
		g_free(line);
		g_warning("ADSP sensor core restarted; reconnecting stream %d", stream->type);
		cleanup_stream(stream);
		return;
	}

	if (stream->type == STREAM_HALL) {
		int closed = decode_hall(line);
		if (closed >= 0)
			update_lid(stream->self, closed);
	}
	const gchar *p = strstr(line, "sensor data:");
	if (p) {
		p += strlen("sensor data:");
		if (stream->type == STREAM_LIGHT) {
			gdouble value = g_ascii_strtod(p, NULL);
			if (isfinite(value) && value >= 0.0) {
				Sensord *s = stream->self;
				set_has_light(s, TRUE);
				/*
				 * Report true factory-calibrated SI lux with adaptive deadband:
				 *  - Always emit the first valid sample (!s->light_valid).
				 *  - Always emit transitions to 0.0 lux from any >0 reading so
				 *    pitch-dark rooms are never trapped behind the deadband.
				 *  - Never spam 5 Hz signals when steady at 0.0 lux.
				 */
				gdouble threshold = fmax(0.5, s->light * 0.05);
				if (!s->light_valid ||
				    (value == 0.0 && s->light > 0.0) ||
				    fabs(value - s->light) >= threshold) {
					s->light = value;
					s->light_valid = TRUE;
					emit_property(s, "LightLevel",
						      g_variant_new_double(value));
				}
			}
		} else if (stream->type == STREAM_ACCEL) {
			gdouble x = 0, y = 0, z = 0;
			if (sscanf(p, "%lf,%lf,%lf", &x, &y, &z) == 3) {
				set_has_accel(stream->self, TRUE);
				accel_process(stream->self, x, y, z);
			}
		}
	}

	g_free(line);
	g_data_input_stream_read_line_async(dis, G_PRIORITY_DEFAULT, NULL,
					    read_stream_cb, stream);
}

static gchar *lookup_suid(const gchar *type)
{
	gchar *argv[] = { (gchar *)SENSH, "lookup", (gchar *)type, NULL };
	GError *error = NULL;
	GPid pid;
	gint fd, status;
	gchar *suid = NULL;
	GString *out = g_string_new(NULL);

	if (!g_spawn_async_with_pipes(NULL, argv, NULL,
			G_SPAWN_DO_NOT_REAP_CHILD | G_SPAWN_STDERR_TO_DEV_NULL,
			NULL, NULL, &pid, NULL, &fd, NULL, &error)) {
		g_warning("sensh lookup %s: %s", type, error->message);
		g_error_free(error);
		g_string_free(out, TRUE);
		return NULL;
	}
	gint64 deadline = g_get_monotonic_time() + 1500 * G_TIME_SPAN_MILLISECOND;
	while (g_get_monotonic_time() < deadline) {
		struct pollfd pfd = { .fd = fd, .events = POLLIN };
		int ms = (int)((deadline - g_get_monotonic_time() + 999) / 1000);
		if (ms <= 0)
			break;
		int result = poll(&pfd, 1, ms);
		if (result < 0 && errno == EINTR)
			continue;
		if (result <= 0 || !(pfd.revents & (POLLIN | POLLHUP)))
			break;
		gchar buf[512];
		ssize_t n = read(fd, buf, sizeof(buf));
		if (n < 0 && errno == EINTR)
			continue;
		if (n <= 0)
			break;
		g_string_append_len(out, buf, n);
		gchar *start = strstr(out->str, "sensor found:");
		if (start) {
			start += strlen("sensor found:");
			while (*start == ' ' || *start == '\t')
				start++;
			gchar *end = start;
			while (*end && !g_ascii_isspace(*end))
				end++;
			if (*end && end != start) {
				suid = g_strndup(start, end - start);
				break;
			}
		}
		if (out->len > 8192)
			break;
	}
	close(fd);
	kill(pid, SIGKILL);
	while (waitpid(pid, &status, 0) < 0 && errno == EINTR)
		;
	g_spawn_close_pid(pid);
	g_string_free(out, TRUE);
	return suid;
}

static Stream *start_stream(Sensord *self, StreamType type, const gchar *suid,
			    const gchar *rate)
{
	GError *error = NULL;
	Stream *stream = g_new0(Stream, 1);

	stream->self = self;
	stream->type = type;
	if (type == STREAM_HALL) {
		stream->proc = g_subprocess_new(G_SUBPROCESS_FLAGS_STDOUT_PIPE |
				G_SUBPROCESS_FLAGS_STDERR_SILENCE, &error,
				SENSH, "watch", suid, NULL);
	} else {
		stream->proc = g_subprocess_new(G_SUBPROCESS_FLAGS_STDOUT_PIPE |
				G_SUBPROCESS_FLAGS_STDERR_SILENCE, &error,
				SENSH, "config", suid, rate, NULL);
	}
	if (!stream->proc) {
		g_warning("start sensh stream: %s", error->message);
		g_error_free(error);
		g_free(stream);
		return NULL;
	}
	stream->out = g_data_input_stream_new(
		g_subprocess_get_stdout_pipe(stream->proc));
	g_data_input_stream_set_newline_type(stream->out,
					     G_DATA_STREAM_NEWLINE_TYPE_ANY);
	g_data_input_stream_read_line_async(stream->out, G_PRIORITY_DEFAULT,
					    NULL, read_stream_cb, stream);
	return stream;
}

static gboolean maintain_sensors(gpointer user_data)
{
	Sensord *self = user_data;

	if (!self->streams[STREAM_LIGHT]) {
		gchar *light = lookup_suid("ambient_light");
		if (light) {
			g_message("ambient_light SUID %s", light);
			self->streams[STREAM_LIGHT] =
				start_stream(self, STREAM_LIGHT, light, "5");
			g_free(light);
			if (self->streams[STREAM_LIGHT])
				set_has_light(self, TRUE);
		}
	}

	if (!self->streams[STREAM_ACCEL]) {
		gchar *accel = lookup_suid("accel");
		if (accel) {
			g_message("accel SUID %s", accel);
			self->streams[STREAM_ACCEL] =
				start_stream(self, STREAM_ACCEL, accel, "25");
			g_free(accel);
			if (self->streams[STREAM_ACCEL])
				set_has_accel(self, TRUE);
		}
	}

	if (!self->streams[STREAM_HALL]) {
		gchar *hall = lookup_suid("hall");
		if (hall) {
			g_message("hall SUID %s", hall);
			self->streams[STREAM_HALL] =
				start_stream(self, STREAM_HALL, hall, NULL);
			g_free(hall);
		}
	} else if (self->lid_fd < 0 && self->lid_state >= 0) {
		update_lid(self, self->lid_state);
	}
	return G_SOURCE_CONTINUE;
}

static GVariant *get_property(GDBusConnection *connection,
			      const gchar *sender, const gchar *object_path,
			      const gchar *interface_name,
			      const gchar *property_name, GError **error,
			      gpointer user_data)
{
	Sensord *self = user_data;
	(void)connection;
	(void)sender;
	(void)object_path;
	(void)interface_name;

	if (g_strcmp0(property_name, "HasAccelerometer") == 0)
		return g_variant_new_boolean(self->has_accel);
	if (g_strcmp0(property_name, "AccelerometerOrientation") == 0)
		return g_variant_new_string(self->orientation ?: "normal");
	if (g_strcmp0(property_name, "AccelerometerTilt") == 0)
		return g_variant_new_string("undefined");
	if (g_strcmp0(property_name, "HasAmbientLight") == 0)
		return g_variant_new_boolean(self->has_light);
	if (g_strcmp0(property_name, "LightLevelUnit") == 0)
		return g_variant_new_string("lux");
	if (g_strcmp0(property_name, "LightLevel") == 0)
		return g_variant_new_double(self->light);
	if (g_strcmp0(property_name, "HasProximity") == 0)
		return g_variant_new_boolean(FALSE);
	if (g_strcmp0(property_name, "ProximityNear") == 0)
		return g_variant_new_boolean(FALSE);

	g_set_error(error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED,
		    "Unknown property %s", property_name);
	return NULL;
}

static void method_call(GDBusConnection *connection, const gchar *sender,
			const gchar *object_path, const gchar *interface_name,
			const gchar *method_name, GVariant *parameters,
			GDBusMethodInvocation *invocation, gpointer user_data)
{
	Sensord *self = user_data;
	(void)connection;
	(void)sender;
	(void)object_path;
	(void)parameters;

	if (g_strcmp0(interface_name, IFACE) == 0) {
		if (g_strcmp0(method_name, "ClaimAccelerometer") == 0) {
			self->accel_claimed = TRUE;
			g_message("ClaimAccelerometer (%s, orientation=%s)",
				  sender ?: "unknown", self->orientation ?: "normal");
			if (self->orientation) {
				set_spk_rotation(self->orientation);
				emit_property(self, "AccelerometerOrientation",
					      g_variant_new_string(self->orientation));
			}
		} else if (g_strcmp0(method_name, "ReleaseAccelerometer") == 0) {
			self->accel_claimed = FALSE;
			g_message("ReleaseAccelerometer (%s)", sender ?: "unknown");
		} else if (g_strcmp0(method_name, "ClaimLight") == 0) {
			self->light_claimed = TRUE;
			if (self->has_light && self->light_valid)
				emit_property(self, "LightLevel",
					      g_variant_new_double(self->light));
		} else if (g_strcmp0(method_name, "ReleaseLight") == 0) {
			self->light_claimed = FALSE;
		}
	}

	g_dbus_method_invocation_return_value(invocation, NULL);
}

static const GDBusInterfaceVTable vtable = {
	.method_call = method_call,
	.get_property = get_property,
	.set_property = NULL,
};

static GVariant *compass_get_property(GDBusConnection *connection,
				      const gchar *sender,
				      const gchar *object_path,
				      const gchar *interface_name,
				      const gchar *property_name,
				      GError **error, gpointer user_data)
{
	(void)connection;
	(void)sender;
	(void)object_path;
	(void)interface_name;
	(void)user_data;

	if (g_strcmp0(property_name, "HasCompass") == 0)
		return g_variant_new_boolean(FALSE);
	if (g_strcmp0(property_name, "CompassHeading") == 0)
		return g_variant_new_double(-1.0);

	g_set_error(error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED,
		    "Unknown property %s", property_name);
	return NULL;
}

static void compass_method_call(GDBusConnection *connection,
				const gchar *sender,
				const gchar *object_path,
				const gchar *interface_name,
				const gchar *method_name,
				GVariant *parameters,
				GDBusMethodInvocation *invocation,
				gpointer user_data)
{
	(void)connection;
	(void)sender;
	(void)object_path;
	(void)interface_name;
	(void)method_name;
	(void)parameters;
	(void)user_data;

	g_dbus_method_invocation_return_value(invocation, NULL);
}

static const GDBusInterfaceVTable compass_vtable = {
	.method_call = compass_method_call,
	.get_property = compass_get_property,
	.set_property = NULL,
};

static void on_bus_acquired(GDBusConnection *connection, const gchar *name,
			    gpointer user_data)
{
	Sensord *self = user_data;
	GDBusNodeInfo *info;
	GError *error = NULL;
	(void)name;

	info = g_dbus_node_info_new_for_xml(introspection_xml, &error);
	if (!info) {
		g_warning("introspection: %s", error->message);
		g_error_free(error);
		return;
	}
	self->conn = g_object_ref(connection);
	self->reg_id = g_dbus_connection_register_object(
		connection, OBJ_PATH, info->interfaces[0], &vtable,
		self, NULL, &error);
	g_dbus_node_info_unref(info);
	if (!self->reg_id) {
		g_warning("register object: %s", error->message);
		g_error_free(error);
	}

	info = g_dbus_node_info_new_for_xml(compass_introspection_xml, &error);
	if (!info) {
		g_warning("compass introspection: %s", error->message);
		g_error_free(error);
		return;
	}
	self->compass_reg_id = g_dbus_connection_register_object(
		connection, COMPASS_OBJ_PATH, info->interfaces[0],
		&compass_vtable, self, NULL, &error);
	g_dbus_node_info_unref(info);
	if (!self->compass_reg_id) {
		g_warning("register compass object: %s", error->message);
		g_error_free(error);
	}
}

static void on_name_acquired(GDBusConnection *connection, const gchar *name,
			     gpointer user_data)
{
	Sensord *self = user_data;
	(void)connection;
	(void)name;

	maintain_sensors(self);
	if (!self->streams[STREAM_HALL])
		g_timeout_add_seconds(3, notify_ready, self);
}

int main(void)
{
	Sensord self = { .current_quadrant = -1, .lid_fd = -1, .lid_state = -1 };
	GMainLoop *loop;

	loop = g_main_loop_new(NULL, FALSE);

	self.owner_id = g_bus_own_name(
		G_BUS_TYPE_SYSTEM, BUS_NAME,
		G_BUS_NAME_OWNER_FLAGS_ALLOW_REPLACEMENT | G_BUS_NAME_OWNER_FLAGS_REPLACE,
		on_bus_acquired, on_name_acquired, NULL, &self, NULL);

	g_timeout_add_seconds(5, maintain_sensors, &self);
	g_timeout_add_seconds(12, notify_ready, &self);
	g_main_loop_run(loop);
	g_main_loop_unref(loop);
	return 0;
}
