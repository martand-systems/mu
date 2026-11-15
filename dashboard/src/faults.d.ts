// Test faults: build-time constants, false in every real build (vite.config.ts FAULTS; docs/design/dashboard.md §7.1).
// The minifier removes the code behind a false constant, so no fault reaches share/dashboard/.
declare const __FAULT_ASSUME_FEATURES__: boolean;
declare const __FAULT_KEEP_FRAGMENT__: boolean;
declare const __FAULT_INNER_HTML__: boolean;
declare const __FAULT_DIV_BUTTON__: boolean;
declare const __FAULT_FAINT_TEXT__: boolean;
declare const __FAULT_STYLE_TAG__: boolean;
declare const __FAULT_RADIX_SCROLLAREA__: boolean;
declare const __FAULT_INNER_HTML_LIB__: boolean;
declare const __FAULT_THEME_IN_BUNDLE__: boolean;
declare const __FAULT_CHART_FIXED_COLOURS__: boolean;
declare const __FAULT_BOUNDARY_SHOWS_ERROR__: boolean;
declare const __FAULT_SERVER_DOWN_AS_OFFLINE__: boolean;
declare const __FAULT_COPY_AFTER_AWAIT__: boolean;
declare const __FAULT_INTERPOLATE_GAP__: boolean;
declare const __FAULT_NETWORK_MODE_DEFAULT__: boolean;
declare const __FAULT_REFUSED_AS_OFFLINE__: boolean;
declare const __FAULT_MAC_SWAP_MEM__: boolean;
declare const __FAULT_FAINT_SERIES__: boolean;
declare const __FAULT_COPY_NO_FALLBACK__: boolean;
declare const __FAULT_VIOLATION_OFFLINE__: boolean;
declare const __FAULT_PUSH_FRAGMENT__: boolean;
declare const __FAULT_PAUSED_AS_RUNNING__: boolean;
declare const __FAULT_COUNT_PAUSED_IN_RUN__: boolean;
declare const __FAULT_PAUSED_CPU_ZERO__: boolean;
