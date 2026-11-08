"""Random tokens where superhistory reads a value by its shape alone, and the code names it shows there.

The readers that judge a lone value by its shape (after "pass" or "pwd" beside a user, in an XML element, after a name
a colour code splits, in a two-item list, beside a user in a mapping, in a field with no label, typed into the focused
field, and after Authorization with no scheme) showed a token they read as a code name, and many random tokens of
letters and digits read as one: of 2,000 random tokens of 20, main 33c82f6 showed 317 in "<secret>...</secret>", 287
after "pass=" beside a user and 66 after "Authorization:". A token of 16 characters or more now reads as a name only
where its words read more like code's than random characters do (kb.mask._reads_as_code, from a table of how code's
words spell, tools/code_letter_pairs.py), or where each of its parts is in one case, as snake case and constants are.
"""
import base64
import json
import os
import random
import string
import subprocess
import sys
import time
import unittest

from kb import mask as masking
from kb.mask import mask, mask_all
from tests.test_mask_dates import header_forms, shape_alone_forms

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALNUM = string.ascii_letters + string.digits


def tokens(shape, n):
    """`n` random tokens of `shape`, the same each run."""
    r = random.Random(f"random tokens: {shape}")
    make = {"20 letters and digits": lambda: "".join(r.choice(ALNUM) for _ in range(20)),
            "32 letters and digits": lambda: "".join(r.choice(ALNUM) for _ in range(32)),
            "base64url of 40 bytes": lambda: base64.urlsafe_b64encode(r.randbytes(40)).decode().rstrip("="),
            "32 hex": lambda: r.randbytes(16).hex(),
            "20 letters": lambda: "".join(r.choice(string.ascii_letters) for _ in range(20))}[shape]
    return [make() for _ in range(n)]


def readings(value):
    """Every place a value is read by its shape alone: (form, what it masks to, what it is when shown)."""
    texts, held = shape_alone_forms(value)
    texts.update(header_forms(value))
    for form, text in texts.items():
        yield form, mask(text), text
    held["headers"] = {"headers": {"Authorization": value}}
    for form, item in held.items():
        yield form, json.dumps(mask_all(item)), json.dumps(item)
    yield "field with no label", json.dumps(masking.typed_secrets("browser_type", {"text": value})), \
        json.dumps({"text": value})


# Kubernetes fields, environment names and calls that hold a secret's word, as manifests, clients and scripts name them.
KUBERNETES_AND_ENV = (
    'secretName', 'secretKeyRef', 'configMapKeyRef', 'serviceAccountName', 'imagePullSecrets',
    'automountServiceAccountToken', 'tlsSecretName', 'clientSecretRef', 'passwordSecretRef',
    'credentialsSecretRef', 'existingSecret', 'existingSecretPasswordKey', 'adminPasswordSecretKeyRef',
    'rootPasswordSecretName', 'caBundleSecretRef', 'serviceAccountTokenSecretName', 'bootstrapTokenSecret',
    'secretProviderClass', 'nodePublishSecretRef', 'controllerExpandSecretRef', 'storageSecretName',
    'tokenSecretRef', 'apiKeySecretRef', 'webhookClientConfig', 'persistentVolumeClaimRetentionPolicy',
    'terminationGracePeriodSeconds', 'readinessProbe', 'livenessProbe', 'initialDelaySeconds',
    'topologySpreadConstraints', 'podAntiAffinity', 'requiredDuringSchedulingIgnoredDuringExecution',
    'preferredDuringSchedulingIgnoredDuringExecution', 'allowPrivilegeEscalation', 'readOnlyRootFilesystem',
    'runAsNonRoot', 'seccompProfile', 'volumeClaimTemplates', 'externalTrafficPolicy', 'sessionAffinityConfig',
    'loadBalancerSourceRanges', 'ingressClassName', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
    'GOOGLE_APPLICATION_CREDENTIALS', 'AZURE_CLIENT_SECRET', 'GITHUB_TOKEN', 'POSTGRES_PASSWORD_FILE',
    'MYSQL_ROOT_PASSWORD', 'REDIS_PASSWORD_SECRET_NAME', 'KUBERNETES_SERVICE_HOST', 'os.path.join', 'json.dumps',
    'subprocess.check_output', 'requests.Session.mount', 'yaml.safe_load', 'getServiceAccountTokenSecret',
    'createNamespacedSecret', 'readNamespacedSecretWithHttpInfo', 'listNamespacedPersistentVolumeClaim',
    'patchNamespacedCustomObjectStatus', 'V1PersistentVolumeClaimSpec', 'V1beta1CertificateSigningRequestSpec',
    'IoK8sApiCoreV1SecretKeySelector',
)
# Names of 16 characters or more from real code, 24 from each source, drawn at random from those main shows.
CODE_NAMES = (
    # C headers
    'DEVLINK_ATTR_REGION_DIRECT', 'FS_DQ_TIMER_MASK', 'INT_N_SEP_BY_SPACE', 'NFS_DEF_ACDIRMAX',
    'OPENSSL_sk_delete', 'PARPORT_IRQ_AUTO', 'PY_MONITORING_EVENT_PY_THROW', 'PyObject_CopyData',
    'SNDRV_SEQ_EVENT_BOUNCE', 'TR_RCF_LIMITED_BROADCAST', 'TypeVarBoundBlock', 'VHOST_VDPA_GET_VRING_GROUP',
    '_M_insert_repeat', '__G_RESOURCE_H__', '__IFLA_VLAN_QOS_MAX', '__glibcxx_check_valid_constructor_range',
    'bmNtbFormatsSupported', 'disable_dontaudit', 'g_dtls_connection_get_ciphersuite_name', 'indistinguishable',
    'kDisableCLIOptions', 'line_input.reserved', 'mld_icmp6_hdr.icmp6_type', 'ossl_der_oid_id_aes256_wrap',
    # JavaScript
    'UserSourcesChanged', '_adoptRequestFromNewProcess', '_parseOptionsEnv', '_saveSerializedSettings',
    '_scrollRectIntoViewIfNeeded', '_updateEmulateMedia', '_waitForAllPagesToBeInitialized',
    'allowNonzeroExitCode', 'blockedbyresponse', 'browserServerImpl', 'cleanupArtifactsDir',
    'deviceorientationabsolute', 'elementSafeTagName', 'emptyStreamTransform', 'getMimeTypeForPath',
    'gl_MultiTexCoord6', 'hasExplicitAriaDisabled', 'idealSelectorForParent', 'import_crCoverage',
    'insertSingleQuoteIgnoringSubstitution', 'keyboard_default', 'longestGlobalOptionTermLength',
    'recordHarOptions', 'resourceTitleString',
    # JavaScript calls
    'E.tsParenthesizedType', 'NU.fetchWithTimeout', 'Page.webSocketFrameReceived', 'accumulated.push',
    'additionalTokens.join', 'boundingBox.width', 'config.testMatch', 'configInternal.configCLIOverrides.debug',
    'e.memoizedState.dehydrated', 'e.options.timers', 'e.rootElements.push', 'fs.promises.readFile',
    'h.TSSatisfiesExpression', 'import_util.promisify', 'import_validatorPrimitives.scheme.PageKeyboardTypeResult',
    'je.prototype.getField', 'options.userAgent', 'testCase.children.length', 'this._accessibilityManager',
    'this._workerParams', 'this._workers.set', 'this.pushSubstring', 'this.vert.firstChild.style.height',
    'v.assertTSQualifiedName',
    # manual pages' environment names
    'BORG_LIBZSTD_PREFIX', 'BPF_F_EXCLUDE_INGRESS', 'CONFIG_CTRL_IFACE_DBUS_NEW', 'CONFIG_GLUSTERFS_ZEROFILL',
    'CONFIG_PROVE_LOCKING', 'CONFIG_VM_EVENT_COUNTERS', 'CUPS_MAX_RUN_TIME', 'EF_PPC_RELOCATABLE_LIB',
    'KERNEL_INSTALL_CONF_ROOT', 'MCAST_JOIN_SOURCE_GROUP', 'NODE_REPL_EXTERNAL_MODULE', 'NS_GET_OWNER_UID',
    'OSSL_DRBG_PARAM_PROPERTIES', 'OSSL_ENCODER_PARAM_PROPERTIES', 'OSSL_FUNC_CIPHER_GET_PARAMS',
    'OSSL_FUNC_DIGEST_DUPCTX', 'OSSL_FUNC_KDF_FREECTX', 'OSSL_PKEY_PARAM_ENGINE', 'OSSL_PKEY_PARAM_FFC_Q',
    'PAM_TIMESTAMP_CHECK', 'RLIMIT_SIGPENDING', 'SANE_DEBUG_CANON', 'SD_SYSTEMD_PORTABLE_RUNTIME',
    'SYSTEMD_LOG_RATELIMIT_KMSG',
    # Python
    'AT_BEGINNING_STRING', 'FILE_FLAG_FIRST_PIPE_INSTANCE', 'GzipDecodedResponse', 'StartNamespaceDeclHandler',
    'TestFrozenUtf8_2', '_AssertRaisesBaseContext', '_FH_LAST_MOD_TIME', '_bless_my_loader', '_check_arg_types',
    '_get_unconnected_pipe', '_make_subprocess_transport', '_markedsectionclose', '_read_dist_info_reqs',
    '_stopped_serving', 'assertAlmostEqual', 'disable_nagle_algorithm', 'ehlo_or_helo_if_needed',
    'get_object_traceback', 'get_proxy_response_headers', 'getallmatchingheaders', 'getpreferredencoding',
    'parent_module_name', 'remove_formatted', 'set_spawning_popen',
    # Python calls
    'ValuesView.register', '_TLSContentType.HEADER', 'checked_value.replace',
    'cls.__non_callable_proto_members__.add', 'context.verify_mode', 'other._microseconds', 'ownerclass.__name__',
    'raw_data_manager.add_set_handler', 'self.__nonlocals', 'self._append_chunk', 'self._process.join',
    'self._remove_writer', 'self._root.findtext', 'self._ssl_protocol._get_write_buffer_size',
    'self._wakeup_waiter', 'self.atTime.hour', 'self.common_funny', 'self.create_stats',
    'self.decode_request_content', 'self.handle_xmlrpc', 'self.parse_float', 'server._accept_pipe_future',
    'stringprep.in_table_c6', 'xml.dom.WrongDocumentErr',
    # Python's constants
    'ALERT_DESCRIPTION_UNSUPPORTED_CERTIFICATE', 'BINARY_SUBSCR_TUPLE_INT', 'CALL_NO_KW_BUILTIN_O',
    'CATEGORY_UNI_NOT_LINEBREAK', 'COVERAGE_REPORT_OPTIONS', 'HAVE_ALIGNED_REQUIRED', 'HAVE_DECL_RTLD_DEEPBIND',
    'HAVE_GETC_UNLOCKED', 'HAVE_GETRANDOM_SYSCALL', 'HAVE_RL_COMPDISP_FUNC_T', 'HAVE_SYS_ENDIAN_H',
    'HAVE_SYS_SELECT_H', 'HAVE_SYS_SYSCALL_H', 'HOSTFLAG_NEVER_CHECK_SUBJECT', 'INSTRUMENTED_LOAD_SUPER_ATTR',
    'INTRINSIC_TYPEVAR_WITH_BOUND', 'KDE_FULL_SESSION', 'LOAD_ATTR_INSTANCE_VALUE', 'MODULE_SELECT_STATE',
    'NB_INPLACE_SUBTRACT', 'NORMAL_PRIORITY_CLASS', 'PGO_PROF_USE_FLAG', 'POSSESSIVE_REPEAT', 'THREAD_STACK_SIZE',
    # TypeScript types
    'AnyClientBulkWriteModel', 'AssociateDistributionWebACLCommand', 'ConnectionGroupAssociationFilter',
    'CreditGrantCreateParams.ApplicabilityConfig', 'DefaultGlobalError', 'FutureRequirements.Entry',
    'NullTypeEquality', 'ObjectTypePropertyKind', 'Refund.NextAction', 'RelevantCompilerOptions',
    'Stripe_.CouponCreateParams.CurrencyOptions', 'Stripe_.PaymentIntentCreateParams.PaymentMethodOptions.Giropay',
    'SuppressionValidationOptions', 'WorkUnitAsyncStorage', 'awsIdentityProperties', 'balance_settings',
    'ignoreParameters', 'includeExternalModuleExports', 'modelcontextprotocol', 'multibanco_display_details',
    'require_payment_method_support', 'suedtirolmyspreadshopxn', 'validateWaiterOptions',
    'warnAboutPreferredRegion',
)


class RandomTokenTest(unittest.TestCase):
    def test_a_random_token_where_main_read_a_name_is_masked(self):
        # Sync's shapes, 2,000 of each, in the three places its counts came from
        for shape in ("20 letters and digits", "32 letters and digits", "base64url of 40 bytes", "32 hex"):
            shown = {}
            for value in tokens(shape, 2000):
                for form, text in (("xml", f"<secret>{value}</secret>"), ("pass", f"user=ci pass={value}"),
                                   ("authorization", f"Authorization: {value}")):
                    if value in mask(text):
                        shown.setdefault(form, []).append(value)
            with self.subTest(shape=shape):
                self.assertEqual({form: values[:3] for form, values in shown.items()}, {})

    def test_a_random_token_is_masked_in_every_reading(self):
        for shape in ("20 letters and digits", "32 letters and digits", "base64url of 40 bytes"):
            for value in tokens(shape, 100):
                for form, masked, _ in readings(value):
                    with self.subTest(shape=shape, value=value, form=form):
                        self.assertNotIn(value, masked)

    def test_random_letters_alone_are_masked_but_for_the_few_that_read_as_code(self):
        # 20 random letters of both cases, with no digit, which main showed every time: about 1 in 1,000 reads as
        # code's words (18 of 20,000), the price of showing code's names
        shown = sorted({value for value in tokens("20 letters", 1000)
                        for text in (f"<secret>{value}</secret>", f"user=ci pass={value}", f"Authorization: {value}")
                        if value in mask(text)})
        self.assertLessEqual(len(shown), 5, shown)


# Names main 33c82f6 masks too, and where: a digit that runs into a word reads as a password's ("Io8s", "V1beta1"), and
# a name with a digit, after Authorization with no scheme, as a credential.
MAIN_MASKS = {"IoK8sApiCoreV1SecretKeySelector": "everywhere", "V1beta1CertificateSigningRequestSpec": "everywhere",
              "V1PersistentVolumeClaimSpec": "after Authorization", "GNU_PROPERTY_X86_FEATURE_1_IBT": "after Authorization",
              "OSSL_DEPRECATEDIN_3_0": "after Authorization", "x86_cpu_VPCLMULQDQ": "after Authorization"}
AFTER_AUTHORIZATION = frozenset(set(header_forms("x")) - {"typed"} | {"headers"})


class CodeNameTest(unittest.TestCase):
    """Code names are masked or shown as main 33c82f6 masks or shows them, byte for byte, in every reading."""

    def assertShown(self, names):
        for name in names:
            for form, masked, shown in readings(name):
                with self.subTest(name=name, form=form):
                    where = MAIN_MASKS.get(name)
                    if where == "everywhere" or where and form in AFTER_AUTHORIZATION:
                        self.assertEqual(masked, shown.replace(name, "[masked]"))
                    else:
                        self.assertEqual(masked, shown)

    def test_kubernetes_fields_and_environment_names_stay(self):
        self.assertShown(KUBERNETES_AND_ENV)

    def test_names_from_real_code_stay(self):
        self.assertShown(CODE_NAMES)

    def test_names_in_one_case_a_part_stay(self):
        self.assertShown(("GNU_PROPERTY_X86_FEATURE_1_IBT", "x86_cpu_VPCLMULQDQ", "SN_kuznyechik_cfb",
                          "OSSL_DEPRECATEDIN_3_0", "max_retry_count_total"))


class LetterPairTableTest(unittest.TestCase):
    def test_the_table_is_what_the_tool_prints(self):
        printed = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "code_letter_pairs.py")],
                                 capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(masking._CODE_LETTER_PAIRS.split(), printed)

    def test_the_word_list_is_sorted_small_letters_once_each(self):
        with open(os.path.join(ROOT, "tools", "code_words.txt"), encoding="ascii") as f:
            words = f.read().split()
        self.assertEqual(words, sorted(set(words)))
        self.assertTrue(all(word.isalpha() and word.islower() and len(word) >= 2 for word in words))


class TokenCostTest(unittest.TestCase):
    """Reading a token as code costs time in proportion to its length, and to the number of tokens in a text."""

    def test_each_doubling_costs_about_double(self):
        r = random.Random("token cost")
        for name, make in (("one long token", lambda size: "<secret>" + "".join(r.choice(ALNUM) for _ in range(size))
                            + "</secret>"),
                           ("many tokens", lambda size: "".join(
                               f"user=ci pass={''.join(r.choice(ALNUM) for _ in range(20))}\n"
                               for _ in range(size // 34)))):
            took = []
            for size in (16, 32, 64, 128):
                text = make(size * 1024)
                started = time.process_time()
                mask(text)
                took.append(time.process_time() - started)
            with self.subTest(shape=name, took=[round(t, 2) for t in took]):
                self.assertLess(took[3], 16 * took[0])
                self.assertLess(took[3], 3 * took[2])


class RevisionTest(unittest.TestCase):
    def test_stored_rows_are_masked_again(self):
        self.assertEqual(masking.REVISION, 21)


if __name__ == "__main__":
    unittest.main()
