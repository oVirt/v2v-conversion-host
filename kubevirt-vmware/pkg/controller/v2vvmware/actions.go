package v2vvmware

import (
	"context"
	"errors"
	"fmt"

	corev1 "k8s.io/api/core/v1"

	kubevirtv1alpha1 "github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/apis/kubevirt/v1alpha1"
	"github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/controller/utils"

	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

func getConnectionSecret(r *ReconcileV2VVmware, request reconcile.Request, instance *kubevirtv1alpha1.V2VVmware) (*corev1.Secret, error) {
	if instance.Spec.Connection == "" {
		return nil, errors.New("the Spec.Connection is required in a V2VVmware object. References a Secret by name")
	}

	secret := &corev1.Secret{}
	err := r.client.Get(context.TODO(), types.NamespacedName{Name: instance.Spec.Connection, Namespace: request.Namespace}, secret)
	return secret, err
}

func getLoginCredentials(connectionSecret *corev1.Secret) (*LoginCredentials) {
	data := connectionSecret.Data

	credentials := &LoginCredentials{
		username: string(data["username"]),
		password: string(data["password"]),
		host: string(data["url"]),
	}

	log.Info(fmt.Sprintf("VMWare credentials retrieved from a Secret, username: '%s', url: '%s'", credentials.username, credentials.host))
	return credentials
}

// read whole list at once
func readVmsList(r *ReconcileV2VVmware, request reconcile.Request, connectionSecret *corev1.Secret) error {
	log.Info("readVmsList()")

	updateStatusPhase(r, request, PhaseConnecting)
	client, err := getClient(context.Background(), getLoginCredentials(connectionSecret))
	if err != nil {
		updateStatusPhase(r, request, PhaseConnectionFailed)
		return err
	}
	defer client.Logout()

	updateStatusPhase(r, request, PhaseLoadingVmsList)
	vmwareVms, thumbprint, err := GetVMs(client)
	if err != nil {
		updateStatusPhase(r, request, PhaseLoadingVmsListFailed)
		return err
	}

	err = updateVmsList(r, request, thumbprint, vmwareVms, utils.MaxRetryCount)
	if err != nil {
		updateStatusPhase(r, request, PhaseLoadingVmsListFailed)
		return err
	}

	updateStatusPhase(r, request, PhaseConnectionSuccessful)
	return nil
}

func updateVmsList(r *ReconcileV2VVmware, request reconcile.Request, thumbprint string, vmwareVms []string, retryCount int) error {
	instance := &kubevirtv1alpha1.V2VVmware{}
	err := r.client.Get(context.TODO(), request.NamespacedName, instance)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to get V2VVmware object to update list of VMs, intended to write: '%s'", vmwareVms))
		if retryCount > 0 {
			utils.SleepBeforeRetry()
			return updateVmsList(r, request, thumbprint, vmwareVms, retryCount - 1)
		}
		return err
	}

	instance.Spec.Vms = make([]kubevirtv1alpha1.VmwareVm, len(vmwareVms))
	for index, vmName := range vmwareVms {
		instance.Spec.Vms[index] = kubevirtv1alpha1.VmwareVm{
			Name:          vmName,
			DetailRequest: false, // can be omitted, but just to be clear
		}
	}
	instance.Spec.Thumbprint = thumbprint

	err = r.client.Update(context.TODO(), instance)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to update V2VVmware object with list of VMWare VMs, intended to write: '%s'", vmwareVms))
		if retryCount > 0 {
			utils.SleepBeforeRetry()
			return updateVmsList(r, request, thumbprint, vmwareVms, retryCount - 1)
		}
		return err
	}

	return nil
}

func readVmDetail(r *ReconcileV2VVmware, request reconcile.Request, connectionSecret *corev1.Secret, vmwareVmName string) (error) {
	log.Info("readVmDetail()")

	updateStatusPhase(r, request, PhaseConnecting)
	client, err := getClient(context.Background(), getLoginCredentials(connectionSecret))
	if err != nil {
		updateStatusPhase(r, request, PhaseConnectionFailed)
		return err
	}
	defer client.Logout()

	updateStatusPhase(r, request, PhaseLoadingVmDetail)

	vmDetail, err := GetVM(client, vmwareVmName)
	if err != nil {
		updateStatusPhase(r, request, PhaseLoadingVmDetailFailed)
		return err
	}

	err = updateVmDetail(r, request, vmwareVmName, vmDetail, utils.MaxRetryCount)
	if err != nil {
		updateStatusPhase(r, request, PhaseLoadingVmDetailFailed)
		return err
	}

	updateStatusPhase(r, request, PhaseConnectionSuccessful)
	return nil
}

func updateVmDetail(r *ReconcileV2VVmware, request reconcile.Request, vmwareVmName string, vmDetail *kubevirtv1alpha1.VmwareVmDetail, retryCount int) (error) {
	instance := &kubevirtv1alpha1.V2VVmware{}
	err := r.client.Get(context.TODO(), request.NamespacedName, instance)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to get V2VVmware object to update detail of '%s' VM.", vmwareVmName))
		if retryCount > 0 {
			utils.SleepBeforeRetry()
			return updateVmDetail(r, request, vmwareVmName, vmDetail, retryCount - 1)
		}
		return err
	}

	for index, vm := range instance.Spec.Vms {
		if  vm.Name == vmwareVmName {
			instance.Spec.Vms[index].DetailRequest = false // skip this detail next time
			instance.Spec.Vms[index].Detail = *vmDetail
		}
	}

	err = r.client.Update(context.TODO(), instance)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to update V2VVmware object with detail of '%s' VM.", vmwareVmName))
		if retryCount > 0 {
			utils.SleepBeforeRetry()
			return updateVmDetail(r, request, vmwareVmName, vmDetail, retryCount - 1)
		}
		return err
	}

	return nil
}

func updateStatusPhase(r *ReconcileV2VVmware, request reconcile.Request, phase string) {
	log.Info(fmt.Sprintf("updateStatusPhase(): %s", phase))
	updateStatusPhaseRetry(r, request, phase, utils.MaxRetryCount)
}

func updateStatusPhaseRetry(r *ReconcileV2VVmware, request reconcile.Request, phase string, retryCount int) {
	// reload instance to workaround issues with parallel writes
	instance := &kubevirtv1alpha1.V2VVmware{}
	err := r.client.Get(context.TODO(), request.NamespacedName, instance)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to get V2VVmware object to update status info. Intended to write phase: '%s'", phase))
		if retryCount > 0 {
			utils.SleepBeforeRetry()
			updateStatusPhaseRetry(r, request, phase, retryCount - 1)
		}
		return
	}

	instance.Status.Phase = phase
	err = r.client.Update(context.TODO(), instance)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to update V2VVmware status. Intended to write phase: '%s'", phase))
		if retryCount > 0 {
			utils.SleepBeforeRetry()
			updateStatusPhaseRetry(r, request, phase, retryCount - 1)
		}
	}
}
