package v2vvmware

import (
	"context"
	"encoding/json"
	"fmt"

	kubevirtv1alpha1 "github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/apis/kubevirt/v1alpha1"
)

/*
  Following code is based on https://github.com/pkliczewski/provider-pod
  modified for the needs of the controller-flow.
*/

func getClient(ctx context.Context, loginCredentials *LoginCredentials) (*Client, error) {
	c, err := NewClient(ctx, loginCredentials)
	if err != nil {
		log.Error(err, "GetVMs: failed to create a client.")
		return nil, err
	}
	return c, nil
}

func GetVMs(c *Client) ([]string, error) {
	vms, err := c.GetVMs()
	if err != nil {
		log.Error(err, "GetVMs: failed to get list of VMs from VMWare.")
		return nil, err
	}

	names := make([]string, len(vms))
	for i, vm := range vms {
		names[i] = vm.Summary.Config.Name
	}

	log.Info(fmt.Sprintf("GetVMs: retrieved list of virtual machines: %s", names))
	return names, nil
}

func GetVM(c *Client, vmName string) (*kubevirtv1alpha1.VmwareVmDetail, error) {
	vm, hostPath, err := c.GetVM(vmName)
	if err != nil {
		log.Error(err, fmt.Sprintf("GetVM: failed to get details of VMWare VM '%s'", vmName))
		return nil, err
	}

	raw, _ := json.Marshal(vm)
	vmDetail := kubevirtv1alpha1.VmwareVmDetail {
		Raw: string(raw), // TODO: pick what's needed
		HostPath: hostPath,
	}
	log.Info(fmt.Sprintf("Fetched VM: %s, host: %s, data: %s", vmName, hostPath, vmDetail.Raw))

	return &vmDetail, nil
}
