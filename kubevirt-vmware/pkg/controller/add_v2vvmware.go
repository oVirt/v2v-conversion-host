package controller

import (
	gc "github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/controller/garbage_collector"
	"github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/controller/gcovirtprovider"
	"github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/controller/ovirtprovider"
	"github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/controller/v2vvmware"
)

func init() {
	// AddToManagerFuncs is a list of functions to create controllers and add them to a manager.
	AddToManagerFuncs = append(AddToManagerFuncs, v2vvmware.Add)
	AddToManagerFuncs = append(AddToManagerFuncs, ovirtprovider.Add)
	AddToManagerFuncs = append(AddToManagerFuncs, gc.GC)
	AddToManagerFuncs = append(AddToManagerFuncs, gcovirtprovider.ProviderGC)
}
