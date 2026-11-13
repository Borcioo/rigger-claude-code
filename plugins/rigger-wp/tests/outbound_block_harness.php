<?php
/**
 * Loads templates/wp-outbound-block.php against small WordPress stand-ins and drives its callbacks.
 *
 * Usage: php outbound_block_harness.php <scenario.json>. Prints one JSON object. The scenario sets:
 * template, log, home, site, constants, use_add_filter, load_twice, abspath, core_mail, calls.
 */

namespace WpOrg\Requests {
	class Exception extends \Exception {
		public function __construct( $message, $type ) {
			parent::__construct( $message );
			$this->type = $type;
		}
		public $type;
	}
}

namespace {
	class WP_Error {
		public $code;
		public $message;
		public function __construct( $code, $message ) {
			$this->code    = $code;
			$this->message = $message;
		}
	}

	$scenario = json_decode( file_get_contents( $argv[1] ), true );

	ini_set( 'log_errors', '1' );
	ini_set( 'error_log', $scenario['log'] );

	function wp_parse_url( $url, $component ) {
		return parse_url( $url, $component );
	}
	function home_url() {
		return $GLOBALS['scenario']['home'];
	}
	function site_url() {
		return $GLOBALS['scenario']['site'];
	}
	function esc_html( $text ) {
		return htmlspecialchars( $text, ENT_QUOTES );
	}

	if ( $scenario['use_add_filter'] ) {
		function add_filter( $hook, $callback, $priority, $accepted_args ) {
			$GLOBALS['wp_filter'][ $hook ][ $priority ][] = array(
				'function'      => $callback,
				'accepted_args' => $accepted_args,
			);
			$GLOBALS['add_filter_calls'] = ( $GLOBALS['add_filter_calls'] ?? 0 ) + 1;
		}
	}

	foreach ( $scenario['constants'] as $name => $value ) {
		define( $name, $value );
	}
	define( 'ABSPATH', $scenario['abspath'] . '/' );
	define( 'WPINC', 'wp-includes' );
	if ( $scenario['core_mail'] ) {
		require ABSPATH . WPINC . '/pluggable.php';
	} else {
		function wp_mail() {
			return false;
		}
	}

	require $scenario['template'];
	if ( $scenario['load_twice'] ) {
		require $scenario['template'];
	}

	function callbacks( $hook ) {
		$found = array();
		foreach ( $GLOBALS['wp_filter'][ $hook ] ?? array() as $priority => $entries ) {
			foreach ( $entries as $entry ) {
				$found[] = array( $priority, $entry );
			}
		}
		return $found;
	}

	function run_hook( $hook, $args ) {
		$only = callbacks( $hook );
		if ( 1 !== count( $only ) ) {
			return array( 'hook_count' => count( $only ) );
		}
		try {
			$result = call_user_func_array( $only[0][1]['function'], $args );
		} catch ( \WpOrg\Requests\Exception $error ) {
			return array( 'thrown' => $error->type, 'message' => $error->getMessage() );
		}
		if ( $result instanceof WP_Error ) {
			return array( 'error' => $result->code, 'message' => $result->message );
		}
		return array( 'value' => $result );
	}

	$out = array(
		'hooks'    => array_map( static fn( $entries ) => array_keys( $entries ), $GLOBALS['wp_filter'] ?? array() ),
		'requests' => array(),
		'redirects' => array(),
		'mail'     => array(),
	);

	foreach ( $scenario['calls']['requests'] ?? array() as $call ) {
		$pre = $call['pre'];
		$out['requests'][] = run_hook( 'pre_http_request', array( $pre, $call['args'] ?? array( 'method' => 'post' ), $call['url'] ) );
	}
	foreach ( $scenario['calls']['redirects'] ?? array() as $location ) {
		$out['redirects'][] = run_hook( 'requests-requests.before_redirect', array( $location ) );
	}
	foreach ( $scenario['calls']['mail'] ?? array() as $call ) {
		$out['mail'][] = run_hook( 'pre_wp_mail', array( $call['pre'], $call['atts'] ) );
	}
	if ( ! empty( $scenario['calls']['plugins_loaded'] ) ) {
		run_hook( 'plugins_loaded', array() );
	}
	if ( ! empty( $scenario['calls']['notices'] ) ) {
		ob_start();
		run_hook( 'admin_notices', array() );
		$out['notice'] = ob_get_clean();
	}
	$out['add_filter_calls'] = $GLOBALS['add_filter_calls'] ?? 0;

	echo json_encode( $out );
}
