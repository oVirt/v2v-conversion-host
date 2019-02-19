package v2vvmware

import (
	"context"
	"fmt"

	kubevirtv1alpha1 "kubevirt.io/v2v-vmware/pkg/apis/kubevirt/v1alpha1"

	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/manager"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
	logf "sigs.k8s.io/controller-runtime/pkg/runtime/log"
	"sigs.k8s.io/controller-runtime/pkg/source"
)

const PhaseConnecting = "Connecting"
const PhaseConnectionSuccessful = "ConnectionVerified"
const PhaseConnectionFailed = "Failed"
const PhaseLoadingVmsList = "LoadingVmsList"
const PhaseLoadingVmsListFailed = "LoadingVmsList"
const PhaseLoadingVmDetail = "LoadingVmDetail"
const PhaseLoadingVmDetailFailed = "LoadingVmDetailFailed"

var log = logf.Log.WithName("controller_v2vvmware")

// TODO: implement garbage collector for V2VVMWare and Secret objects via
// - setting timeToLive label based on presence of "cnv.io/temporary"
// - checking for expiration by "timeToLive"

// Add creates a new V2VVmware Controller and adds it to the Manager. The Manager will set fields on the Controller
// and Start it when the Manager is Started.
func Add(mgr manager.Manager) error {
	return add(mgr, newReconciler(mgr))
}

// newReconciler returns a new reconcile.Reconciler
func newReconciler(mgr manager.Manager) reconcile.Reconciler {
	return &ReconcileV2VVmware{client: mgr.GetClient(), scheme: mgr.GetScheme()}
}

// add adds a new Controller to mgr with r as the reconcile.Reconciler
func add(mgr manager.Manager, r reconcile.Reconciler) error {
	// Create a new controller
	c, err := controller.New("v2vvmware-controller", mgr, controller.Options{Reconciler: r})
	if err != nil {
		return err
	}

	// Watch for changes to primary resource V2VVmware
	err = c.Watch(&source.Kind{Type: &kubevirtv1alpha1.V2VVmware{}}, &handler.EnqueueRequestForObject{})
	if err != nil {
		return err
	}

	return nil
}

var _ reconcile.Reconciler = &ReconcileV2VVmware{}

// ReconcileV2VVmware reconciles a V2VVmware object
type ReconcileV2VVmware struct {
	// This client, initialized using mgr.Client() above, is a split client
	// that reads objects from the cache and writes to the apiserver
	client client.Client
	scheme *runtime.Scheme
}

// Reconcile reads that state of the cluster for a V2VVmware object and makes changes based on the state read
// and what is in the V2VVmware.Spec
// Note:
// The Controller will requeue the Request to be processed again if the returned error is non-nil or
// Result.Requeue is true, otherwise upon completion it will remove the work from the queue.
func (r *ReconcileV2VVmware) Reconcile(request reconcile.Request) (reconcile.Result, error) {
	reqLogger := log.WithValues("Request.Namespace", request.Namespace, "Request.Name", request.Name)
	reqLogger.Info("Reconciling V2VVmware")

	// Fetch the V2VVmware instance
	instance := &kubevirtv1alpha1.V2VVmware{}
	err := r.client.Get(context.TODO(), request.NamespacedName, instance)
	if err != nil {
		if errors.IsNotFound(err) {
			// Request object not found, could have been deleted after reconcile request.
			// Owned objects are automatically garbage collected. For additional cleanup logic use finalizers.
			// Return and don't requeue
			reqLogger.Info("The request object cannot be found.")
			return reconcile.Result{}, nil
		}
		// Error reading the object - requeue the request.
		reqLogger.Info("Error reading the request object, requeuing.")
		return reconcile.Result{}, err
	}

    connectionSecret, err := getConnectionSecret(r, request, instance)
    if err != nil {
    	reqLogger.Error(err, "Failed to get Secret object for the VMWare connection")
		return reconcile.Result{}, err // request will be re-queued
	}
	reqLogger.Info("Connection secret retrieved.")
/* Commented out: based on actual UI flow, the status.phase can be set within readVmsList and load of VMs can be initiated immediately
    if !instance.Spec.ListVmsRequest {
		// true if list of VMWare VMs shall start to be retrieved
		// Imperative hack to enable quick/independent check of credentials in the most simple way

		if instance.Status.Phase == PhaseConnectionSuccessful {
			reqLogger.Info("The checkConnectionOnly() already finished, nothing to do.")
			return reconcile.Result{}, nil
		}

    	err = checkConnectionOnly(r, request, connectionSecret)
    	if err != nil {
			reqLogger.Error(err, "Failed to check VMWare connection.")
		}
		return reconcile.Result{}, err // request will be re-queued if failed
	}
*/
    // Considering recent high-level flow, the list of VMWare VMs is read at most once (means: do not refresh).
    // If refresh is ever needed, implement either here or re-create the V2VVmware object

	if len(instance.Spec.Vms) == 0 { // list of VMWare VMs is requested to be retrieved
		err = readVmsList(r, request, connectionSecret)
		if err != nil {
			reqLogger.Error(err, "Failed to read list of VMWare VMs.")
		}
		return reconcile.Result{}, err // request will be re-queued if failed
	}

    // secret is present, list of VMs is available, let's check for  details to be retrieved
    var lastError error = nil
    for _, vm := range instance.Spec.Vms { // sequential read is probably good enough, just a single VM or a few of them are expected to be retrieved this way
    	if vm.DetailRequest {
			err = readVmDetail(r, request, connectionSecret, vm.Name)
			if err != nil {
				reqLogger.Error(err, fmt.Sprintf("Failed to read detail of '%s' VMWare VM.", vm.Name))
				lastError = err
			}
		}
	}

	return reconcile.Result{}, lastError
}
