package v2vvmware

/*
  Following code is based on https://github.com/pkliczewski/provider-pod
  modified for the needs of the controller-flow.
*/

import (
	"context"
	"fmt"
	"github.com/vmware/govmomi"
	"github.com/vmware/govmomi/find"
	"github.com/vmware/govmomi/property"
	"github.com/vmware/govmomi/view"
	"github.com/vmware/govmomi/vim25"
	"github.com/vmware/govmomi/vim25/mo"
	"github.com/vmware/govmomi/object"
	"net/http"
	"net/url"
)

type Client struct {
	Client *govmomi.Client
	ctx context.Context
}

type LoginCredentials struct {
	host string
	username string
	password string
}

func (c *Client) GetVMs() ([]mo.VirtualMachine, string, error) {
	var thumbprint string

	client := c.Client

	// Get thumbprint
	var info object.HostCertificateInfo
	url := client.Client.URL()
	transport := client.Client.Transport.(*http.Transport)
	err := info.FromURL(url, transport.TLSClientConfig)
	if err != nil {
		return nil, thumbprint, err
	}
	thumbprint = info.ThumbprintSHA1

	// List VMs
	m := view.NewManager(client.Client)

	v, err := m.CreateContainerView(c.ctx, client.ServiceContent.RootFolder, []string{"VirtualMachine"}, true)
	if err != nil {
		return nil, thumbprint, err
	}

	defer v.Destroy(c.ctx)

	// Reference: http://pubs.vmware.com/vsphere-60/topic/com.vmware.wssdk.apiref.doc/vim.VirtualMachine.html
	var vms []mo.VirtualMachine
	err = v.Retrieve(c.ctx, []string{"VirtualMachine"}, []string{"summary"}, &vms)
	if err != nil {
		return nil, thumbprint, err
	}

	return vms, thumbprint, nil
}

func (c *Client) GetVM(name string) (mo.VirtualMachine, string, error) {
	client := c.Client

	m := view.NewManager(client.Client)

	var vm mo.VirtualMachine
	var hostPath string

	v, err := m.CreateContainerView(c.ctx, client.ServiceContent.RootFolder, []string{"VirtualMachine"}, true)
	if err != nil {
		return vm, hostPath, err
	}

	defer v.Destroy(c.ctx)

	// Reference: http://pubs.vmware.com/vsphere-60/topic/com.vmware.wssdk.apiref.doc/vim.VirtualMachine.html
	err = v.RetrieveWithFilter(c.ctx, []string{"VirtualMachine"}, []string{"config", "summary"}, &vm, property.Filter{"summary.config.name": name})
	if err != nil {
		return vm, hostPath, err
	}

	f := find.NewFinder(client.Client, true)
	host, err := f.ObjectReference(c.ctx, *vm.Summary.Runtime.Host)
	if err != nil {
		return vm, hostPath, err
	}
	hostPath = host.(*object.HostSystem).Common.InventoryPath

	return vm, hostPath, nil
}

func (c *Client) Logout() error {
	client := c.Client
	return client.Logout(c.ctx)
}

func NewClient(ctx context.Context, credentials *LoginCredentials) (*Client, error) {
	insecure := true // TODO

	log.Info(fmt.Sprintf("NewClient, user: '%s', host: '%s'", credentials.username, credentials.host))

	u := &url.URL{
		Scheme: "https",
		User:   url.UserPassword(credentials.username, credentials.password),
		Host:   credentials.host, // TODO: handle the case if credentials.host starts with protocol (https://)
		Path:   vim25.Path,
	}

	// Connect and log in to ESX or vCenter
	client, err := govmomi.NewClient(ctx, u, insecure)
	if err != nil {
		return nil, err
	}

	c := &Client{
		Client: client,
		ctx: ctx,
	}
	return c, nil
}
