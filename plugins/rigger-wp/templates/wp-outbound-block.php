<?php
/**
 * Plugin Name: Rigger outbound block
 * Description: Blocks HTTP requests to third-party hosts and wp_mail() on a non-production copy.
 *
 * Local and staging copies only: on production it stops every outgoing API call and e-mail.
 * Needs WordPress 6.2+ (WpOrg\Requests).
 * - Local: copy this file to wp-content/mu-plugins/ and add that path to .git/info/exclude.
 * - Staging or another deployed copy: if the deploy replaces mu-plugins, keep the file outside the
 *   deployed tree and load it from wp-config.php, above the wp-settings.php line:
 *   require '/absolute/path/wp-outbound-block.php';
 * - Do not commit it and never ship it to production.
 *
 * Allowed hosts: localhost, 127.0.0.1, ::1, *.test, *.localhost, the site's own host, wordpress.org
 * and its subdomains, and RIGGER_OUTBOUND_ALLOW (wp-config.php): comma-separated, each entry an exact
 * host or a ".suffix" matching every subdomain, e.g. define( 'RIGGER_OUTBOUND_ALLOW', 'example.com,.example.com' );
 * Everything else, any URL whose host does not parse, and a redirect to a host not allowed get a
 * WP_Error and a log line.
 * Mail: define( 'RIGGER_ALLOW_MAIL', true ) sends through the site's mailer again; point that mailer
 * at a local mail catcher first, the copy may still hold the production SMTP or API key.
 *
 * Not covered, switch these off separately:
 * - SDKs that call curl, Guzzle, sockets or the Requests library directly instead of the WordPress
 *   HTTP API (WP-CLI's own HTTP calls included);
 * - another pre_http_request filter at PHP_INT_MAX that returns false regardless of its input;
 * - a plugin that replaces the pluggable wp_mail() or talks SMTP itself (a log line warns when
 *   wp_mail() is not core's);
 * - pixels and analytics loaded in the browser during hands-on checks: block third-party hosts in
 *   the test browser (Chromium --host-resolver-rules, Playwright page.route / context.route);
 * - the site's own host while siteurl/home still name production (search-replace first).
 */

// Marks a copy already loaded (mu-plugins and wp-config.php both); not a switch.
if ( defined( 'RIGGER_OUTBOUND_BLOCK_FILE' ) ) {
	return;
}
define( 'RIGGER_OUTBOUND_BLOCK_FILE', __FILE__ );

$rigger_outbound_hook = static function ( $hook, $callback, $priority, $accepted_args ) {
	if ( function_exists( 'add_filter' ) ) {
		add_filter( $hook, $callback, $priority, $accepted_args );
		return;
	}
	// Loaded from wp-config.php: wp-includes/plugin.php turns pre-filled entries into hooks.
	$GLOBALS['wp_filter'][ $hook ][ $priority ][] = array(
		'function'      => $callback,
		'accepted_args' => $accepted_args,
	);
};

$rigger_outbound_allowed = static function ( $host ) {
	$host = strtolower( rtrim( trim( $host, '[]' ), '.' ) );
	if ( '' === $host ) {
		return false;
	}
	$exact    = array( 'localhost', '127.0.0.1', '::1', 'wordpress.org' );
	$exact[]  = strtolower( (string) wp_parse_url( home_url(), PHP_URL_HOST ) );
	$exact[]  = strtolower( (string) wp_parse_url( site_url(), PHP_URL_HOST ) );
	$suffixes = array( '.test', '.localhost', '.wordpress.org' );
	if ( defined( 'RIGGER_OUTBOUND_ALLOW' ) ) {
		foreach ( explode( ',', strtolower( (string) RIGGER_OUTBOUND_ALLOW ) ) as $entry ) {
			$entry = rtrim( trim( $entry ), '.' );
			if ( '' === $entry ) {
				continue;
			}
			if ( '.' === $entry[0] ) {
				$suffixes[] = $entry;
			} else {
				$exact[] = $entry;
			}
		}
	}
	if ( in_array( $host, $exact, true ) ) {
		return true;
	}
	foreach ( $suffixes as $suffix ) {
		if ( substr( $host, -strlen( $suffix ) ) === $suffix ) {
			return true;
		}
	}
	return false;
};

$rigger_outbound_mail_allowed = defined( 'RIGGER_ALLOW_MAIL' ) && RIGGER_ALLOW_MAIL;

$rigger_outbound_hook(
	'pre_http_request',
	static function ( $pre, $args, $url ) use ( $rigger_outbound_allowed ) {
		// An earlier filter already answered (a test fake, a cache): no request leaves.
		if ( false !== $pre ) {
			return $pre;
		}
		$host = wp_parse_url( $url, PHP_URL_HOST );
		if ( is_string( $host ) && $rigger_outbound_allowed( $host ) ) {
			return false;
		}
		// Method and host only: the query string can carry API keys.
		$host   = is_string( $host ) ? strtolower( $host ) : '(unparsed host)';
		$method = isset( $args['method'] ) ? strtoupper( (string) $args['method'] ) : 'GET';
		error_log( sprintf( 'rigger-outbound-block: blocked %s %s', $method, $host ) );
		return new WP_Error( 'rigger_outbound_blocked', sprintf( 'Outbound request to %s blocked by rigger-outbound-block.', $host ) );
	},
	PHP_INT_MAX,
	3
);

// Requests follows 3xx itself without running pre_http_request again; WP_Http turns the exception into a WP_Error.
$rigger_outbound_hook(
	'requests-requests.before_redirect',
	static function ( $location ) use ( $rigger_outbound_allowed ) {
		// Requests has already made $location absolute; a host that is empty or does not parse is refused.
		$host = wp_parse_url( $location, PHP_URL_HOST );
		if ( $rigger_outbound_allowed( (string) $host ) ) {
			return;
		}
		$host = is_string( $host ) ? strtolower( $host ) : '(unparsed host)';
		error_log( sprintf( 'rigger-outbound-block: blocked redirect to %s', $host ) );
		throw new WpOrg\Requests\Exception(sprintf( 'Redirect to %s blocked by rigger-outbound-block.', $host ), 'rigger_outbound_blocked' );
	},
	PHP_INT_MAX,
	1
);

$rigger_outbound_hook(
	'pre_wp_mail',
	static function ( $pre, $atts ) use ( $rigger_outbound_mail_allowed ) {
		if ( null !== $pre || $rigger_outbound_mail_allowed ) {
			return $pre;
		}
		$to      = isset( $atts['to'] ) ? $atts['to'] : array();
		$domains = array();
		foreach ( is_array( $to ) ? $to : explode( ',', (string) $to ) as $address ) {
			$at        = strrpos( (string) $address, '@' );
			$domains[] = false === $at ? '?' : strtolower( trim( substr( $address, $at + 1 ), " >\t" ) );
		}
		$subject = isset( $atts['subject'] ) ? preg_replace( '/[\x00-\x1F]+/', ' ', (string) $atts['subject'] ) : '';
		error_log( sprintf( 'rigger-outbound-block: blocked wp_mail to %s, subject "%s"', implode( ',', array_unique( $domains ) ), $subject ) );
		return false;
	},
	PHP_INT_MAX,
	2
);

$rigger_outbound_status = 'HTTP to third-party hosts is blocked, wp_mail() is ' . ( $rigger_outbound_mail_allowed ? 'allowed (RIGGER_ALLOW_MAIL)' : 'blocked' );

// wp-settings.php loads pluggable.php after plugins and before plugins_loaded, so wp_mail() is
// final here; the log line also lands after wp_debug_mode(), in the same log as the block lines.
$rigger_outbound_hook(
	'plugins_loaded',
	static function () use ( $rigger_outbound_status ) {
		error_log( sprintf( 'rigger-outbound-block: active (%s): %s', __FILE__, $rigger_outbound_status ) );
		$file = ( new ReflectionFunction( 'wp_mail' ) )->getFileName();
		if ( realpath( (string) $file ) !== realpath( ABSPATH . WPINC . '/pluggable.php' ) ) {
			error_log( sprintf( 'rigger-outbound-block: WARNING wp_mail() comes from %s, not core: pre_wp_mail may not run and mail can leave', $file ) );
		}
	},
	PHP_INT_MAX,
	0
);

$rigger_outbound_notice = static function () use ( $rigger_outbound_status ) {
	printf( '<div class="notice notice-warning"><p>%s</p></div>', esc_html( 'rigger-outbound-block is active: ' . $rigger_outbound_status . '. It must never run on production.' ) );
};
$rigger_outbound_hook( 'admin_notices', $rigger_outbound_notice, 10, 0 );
$rigger_outbound_hook( 'network_admin_notices', $rigger_outbound_notice, 10, 0 );

unset( $rigger_outbound_hook, $rigger_outbound_allowed, $rigger_outbound_mail_allowed, $rigger_outbound_status, $rigger_outbound_notice );
